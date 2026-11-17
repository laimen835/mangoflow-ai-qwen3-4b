from __future__ import annotations

import re

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import settings
from src.core.db.models import Chunk, Document
from src.core.rag.exceptions import RetrievalError
from src.core.rag.vector_search import SearchHit

_QUERY_STOP = set("кто что какой какая какие какое как где когда сейчас найти находится узнать расскажи покажи пожалуйста это у на в и по из для о об с со во ли или мне нас наш этого этой году год спк скга колледж колледжа среднепрофессионального среднепрофессиональный отделения".split())
_QUERY_STOP.update("был была были есть могут может можно сколько нужно нужны этот эту такие уточнение посмотреть".split())


def query_terms(query: str) -> list[str]:
    return list(dict.fromkeys(t for t in re.findall(r"\d{2}\.\d{2}\.\d{2}(?:-\d{3})?|[а-яa-z0-9]+", query.casefold().replace('ё', 'е'))
            if t not in _QUERY_STOP and len(t) > 2))


async def bm25_search(
    db: AsyncSession,
    query: str,
    limit: int = 20,
    include_test: bool = False,
    unit_keys: tuple[str, ...] = (),
    document_ids: tuple[int, ...] = (),
) -> list[SearchHit]:
    """
    Лексический поиск по content_tsv с ранжированием ts_rank_cd.
    Аналог BM25 для PostgreSQL: учитывает частоту термина и близость слов.
    """
    query = (query or "").strip()
    if not query:
        return []

    # plainto_tsquery безопаснее to_tsquery: не падает на спецсимволах
    terms = query_terms(query)
    if not terms:
        return []
    ts_query = func.websearch_to_tsquery(settings.fts_language, " OR ".join(terms))
    # Include document titles: timetable PDFs often contain only table cells.
    searchable = Chunk.content_tsv.op("||")(func.to_tsvector(settings.fts_language, Document.title))
    rank_expr = func.ts_rank_cd(searchable, ts_query).label("rank")

    try:
        stmt = (
            select(Chunk, Document, rank_expr)
            .join(Document, Document.id == Chunk.document_id)
            .where(searchable.op("@@")(ts_query))
            .where(Document.status == "INDEXED")
            .order_by(rank_expr.desc())
            .limit(limit)
        )
        if unit_keys:
            from src.core.services.academy_scope import unit_filter
            stmt = stmt.where(unit_filter(unit_keys))
        if document_ids:
            stmt = stmt.where(Document.id.in_(document_ids))
        if not include_test:
            from src.core.rag.document_policy import real_document_filter
            stmt = stmt.where(real_document_filter())
        rows = (await db.execute(stmt)).all()
    except Exception as exc:
        raise RetrievalError(f"BM25 search failed: {exc}") from exc

    return [
        SearchHit(
            chunk_id=chunk.id,
            document_id=doc.id,
            content=chunk.content,
            title=doc.title,
            url=doc.source_url,
            rank=rank,
            raw_score=float(score),
            source="bm25",
            meta={**(chunk.meta or {}),'unit_key':(doc.meta or {}).get('unit_key'),'unit_name':(doc.meta or {}).get('unit_name'),'chunk_index':chunk.chunk_index,'document_review':(doc.meta or {}).get('review'),
                  'scholarship_material':(doc.meta or {}).get('scholarship_material',False)},
        )
        for rank, (chunk, doc, score) in enumerate(rows, start=1)
    ]
