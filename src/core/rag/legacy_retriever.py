from __future__ import annotations
import re
import time
from sqlalchemy import select
from src.core.db.models import Document,DocumentStatus
from src.core.rag.document_policy import real_document_filter

from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import Settings
from src.core.rag.bm25_search import bm25_search, query_terms
from src.core.rag.embeddings import EmbeddingProvider
from src.core.rag.rrf import reciprocal_rank_fusion
from src.core.rag.vector_search import SearchHit, vector_search
from src.core.rag.query import expand_query, focus_terms
from src.core.rag.period_policy import period_exclusion


class HybridRetriever:
    def __init__(
        self,
        db: AsyncSession,
        embedder: EmbeddingProvider,
        settings: Settings,
        *, include_test: bool = False, unit_keys: tuple[str, ...] = (),
    ) -> None:
        self._db = db
        self._embedder = embedder
        self._settings = settings
        self._include_test = include_test
        self._unit_keys = unit_keys

    async def search(
        self,
        query: str,
        top_k: int | None = None,
    ) -> list[SearchHit]:
        started = time.perf_counter()
        self.last_diagnostics = {"candidates": []}
        top_k = top_k or self._settings.top_k_final

        # Auxiliary synonyms may improve ranking, but must not add a mandatory
        # focus gate the user never requested (paid places != a price question).
        focus = focus_terms(query)
        document_ids=()
        lexical_query=None
        if getattr(self._settings,'academy_department_navigation',False) and re.search('кафедр',query,re.I) and not re.search(r'сравн|противореч|различ',query,re.I):
            from src.core.services.academy_scope import requested_department_matches,unit_filter
            from src.core.rag.evidence_search import word_terms
            stmt=select(Document.id,Document.meta).where(Document.status==DocumentStatus.INDEXED,
                Document.meta['department_name'].astext.is_not(None))
            if not self._include_test:stmt=stmt.where(real_document_filter())
            if self._unit_keys:stmt=stmt.where(unit_filter(self._unit_keys))
            matches=[(id,meta) for id,meta in (await self._db.execute(stmt)).all()
                     if requested_department_matches(query,meta.get('department_name'))]
            if len(matches)==1:
                id,meta=matches[0];document_ids=(id,)
                identity=set(word_terms(meta['department_name']+' '+meta.get('unit_name','')))|{'кафедра','институт','подразделение'}
                residual=set(word_terms(query))-identity
                # A common source abbreviation is an auxiliary lexical signal.
                if 'кабинет' in residual:residual.add('каб')
                lexical_query=' '.join(sorted(residual)) or query
                self.last_diagnostics['department_document_ids']=list(document_ids)
                self.last_diagnostics['department_fact_query']=lexical_query
        document_filter={'document_ids':document_ids} if document_ids else {}
        query = expand_query(query)
        # 1. Эмбеддинг запроса
        query_embedding = await self._embedder.embed_one(query)

        # 2. AsyncSession нельзя использовать в двух задачах одновременно.
        vector_hits = await vector_search(
            db=self._db,
            query_embedding=query_embedding,
            limit=self._settings.top_k_vector,
            include_test=self._include_test, **({"unit_keys":self._unit_keys} if self._unit_keys else {}),
            **document_filter,
        )
        bm25_hits = await bm25_search(
            db=self._db,
            query=lexical_query or query,
            limit=self._settings.top_k_bm25,
            include_test=self._include_test, **({"unit_keys":self._unit_keys} if self._unit_keys else {}),
            **document_filter,
        )

        focused_hits = []
        if focus:
            focused_hits = await bm25_search(
                db=self._db, query=" ".join(focus),
                limit=self._settings.top_k_bm25, include_test=self._include_test, **({"unit_keys":self._unit_keys} if self._unit_keys else {}),
                **document_filter,
            )
        # 3. Слияние через RRF
        candidates = reciprocal_rank_fusion(
            [vector_hits, bm25_hits, focused_hits],
            k=self._settings.rrf_k,
            top_k=self._settings.top_k_vector + self._settings.top_k_bm25,
        )
        terms = query_terms(query)
        has_keyword_evidence = False
        relevant = []
        for hit in candidates:
            period_reason = period_exclusion(query, hit.meta)
            if period_reason:
                self.last_diagnostics['candidates'].append({'chunk_id': hit.chunk_id, 'title': hit.title,
                    'url': hit.url, 'score': hit.raw_score, 'kept': False, 'reason': period_reason})
                continue
            text = (hit.title + " " + hit.content).casefold().replace('ё', 'е')
            words = re.findall(r"[а-яёa-z0-9]+", text)
            coverage = sum((term in text if "." in term else any(word.startswith(term[:5]) for word in words))
                           for term in terms) / max(len(terms), 1)
            has_keyword_evidence = has_keyword_evidence or coverage > 0
            hit.raw_score += 0.04 * coverage
            hit.raw_score += 0.15 * sum(term in text for term in terms if "." in term)
            focus_coverage = sum(any(word.startswith(term) for word in words) for term in focus)
            # A query about documents for a graduate need not contain the
            # word "graduate" in the answer. Focus is a modest ranking signal,
            # never an independent exclusion or a dominant replacement for RRF.
            hit.raw_score += 0.02 * focus_coverage
            keep = coverage > 0 or not terms
            self.last_diagnostics["candidates"].append({"chunk_id": hit.chunk_id, "title": hit.title, "url": hit.url, "score": hit.raw_score, "kept": keep, "focus_matches": focus_coverage, "reason": "accepted" if keep else "word_gate"})
            if keep:
                relevant.append(hit)
        if terms and not has_keyword_evidence:
            self.last_diagnostics["total_ms"] = (time.perf_counter()-started)*1000
            return []
        candidates = relevant
        if getattr(self._settings,'academy_department_navigation',False):
            from src.core.services.academy_scope import requested_department_matches
            matches={h.chunk_id:requested_department_matches(query,h.meta.get('department_name')) for h in candidates}
            candidates.sort(key=lambda h:(matches[h.chunk_id],h.raw_score),reverse=True)
            self.last_diagnostics['navigation_prioritized']=[h.chunk_id for h in candidates if matches[h.chunk_id]]
        else:
            candidates.sort(key=lambda h: h.raw_score, reverse=True)
        for rank, hit in enumerate(candidates, 1):
            hit.rank = rank
        self.last_diagnostics["total_ms"] = (time.perf_counter()-started)*1000
        return candidates[:top_k]
