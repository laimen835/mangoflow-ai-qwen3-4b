"""Opt-in query planning and BM25/dense fusion on unchanged embedding profiles.

The original request is always searched. Corrections are auxiliary hypotheses,
never replacements for user text, names, years, codes or facts.
"""
from __future__ import annotations

from collections import Counter, OrderedDict
from dataclasses import replace
from difflib import get_close_matches
import re
import time

from sqlalchemy import select, func, or_
from src.core.db.models import Chunk, Document, DocumentStatus
from src.core.rag.bm25_search import bm25_search
from src.core.rag.document_policy import real_document_filter
from src.core.rag.evidence_search import Catalog, Entry, normal_word, score_catalog, word_terms
from src.core.rag.period_policy import period_exclusion
from src.core.rag.profiles import prepare
from src.core.rag.query import expand_query
from src.core.rag.rrf import reciprocal_rank_fusion
from src.core.rag.vector_search import SearchHit, vector_search

_CATALOG_CACHE = OrderedDict()


def scoped_fact_query(question,unit_keys):
    from src.core.services.academy_scope import UNITS
    tokens=list(re.finditer(r'[а-яёa-z]+',question,re.I))
    lemmas=[normal_word(t.group()) for t in tokens]
    patterns=[]
    for unit in UNITS:
        if unit.key not in unit_keys:
            continue
        names=[unit.name]+[a for a in unit.aliases if a in {'ицт','пмиит','спк','спо','вуц','идил','иоо','иэиу'}]
        patterns.extend([normal_word(w) for w in re.findall(r'[а-яёa-z]+',name,re.I)] for name in names)
    spans=[]
    index=0
    while index<len(tokens):
        match=next((p for p in sorted(patterns,key=len,reverse=True) if p and lemmas[index:index+len(p)]==p),None)
        if match:
            spans.append((tokens[index].start(),tokens[index+len(match)-1].end()))
            index+=len(match)
        else:
            index+=1
    result=question
    for start,end in reversed(spans):
        result=result[:start]+' '+result[end:]
    result=re.sub(r'\s+',' ',result).strip()
    return result if word_terms(result) else question


def requested_source_format(question):
    if re.search(r'ссылк|скачат|где найти файл',question,re.I):
        return None
    return 'pdf' if re.search(r'\b(?:по|в|из)\s+(?:опубликованн[а-я]*\s+)?(?:pdf|пдф)\b',question,re.I) else None


def query_plan(question, vocabulary,unit_keys=()):
    variants = [question]
    fact_query=scoped_fact_query(question,unit_keys) if unit_keys else question
    if fact_query!=question:
        variants.append(fact_query)
    expanded = expand_query(fact_query)
    hypotheses = []
    corrections = {}
    words = re.findall(r'\d{2}\.\d{2}\.\d{2}(?:-\d{3})?|[А-Яа-яЁёA-Za-z]+|\d+', question)
    for word in words:
        # Case-marked names, abbreviations and every numeric entity stay exact.
        if not re.fullmatch(r'[а-яё]{5,}', word) or normal_word(word) in vocabulary:
            continue
        nearby = get_close_matches(normal_word(word), vocabulary, n=2, cutoff=.84)
        # Ambiguous fuzzy matches are not silently resolved to one person/term.
        if len(nearby) == 1:
            corrections[word] = nearby[0]
            hypotheses.append(nearby[0])
    rules = [(r'общаг|ночлег','общежитие'), (r'стипух|степенд','стипендия'),
             (r'препод','преподаватель'), (r'поступить|поступл','прием абитуриент'),
             (r'матан','математический анализ'), (r'дистанцион|удален','электронное обучение')]
    hypotheses.extend(value for pattern,value in rules if re.search(pattern, question, re.I))
    if hypotheses:
        expanded+='\nПоисковые варианты: '+' '.join(dict.fromkeys(hypotheses))
    if expanded!=question:
        variants.append(expanded)
    return list(dict.fromkeys(variants))[:3], corrections


async def lexical_catalog(db, *, unit_keys=(), include_test=False,document_format=None):
    condition = [Document.status==DocumentStatus.INDEXED]
    if not include_test:
        condition.append(real_document_filter())
    if unit_keys:
        from src.core.services.academy_scope import unit_filter
        condition.append(unit_filter(unit_keys))
    if document_format=='pdf':
        condition.append(or_(Document.source_type=='ncsa_pdf',Document.file_mime=='application/pdf',
            Document.meta.contains({'document_type':'pdf'}),Document.source_url.ilike('%.pdf')))
    stamp=(await db.execute(select(func.count(Chunk.id),func.max(Chunk.id),func.max(Document.updated_at))
        .select_from(Chunk).join(Document,Document.id==Chunk.document_id).where(*condition))).one()
    import hashlib
    bind=db.get_bind()
    binding=hashlib.sha256(getattr(bind,'engine',bind).url.render_as_string(hide_password=False).encode()).hexdigest()
    key=(binding,tuple(sorted(unit_keys)),include_test,document_format)
    cached=_CATALOG_CACHE.get(key)
    if cached and cached[0]==stamp and time.monotonic()-cached[1]<30:
        return cached[2]
    rows = (await db.execute(select(Chunk.id, Chunk.document_id, Chunk.chunk_index, Chunk.content, Chunk.meta,
        Document.title, Document.source_url, Document.meta).join(Document,Document.id==Chunk.document_id)
        .where(*condition).order_by(Chunk.id))).all()
    entries, frequencies = [], Counter()
    for cid,did,index,content,cm,title,url,dm in rows:
        meta = {**(cm or {}), 'chunk_index':index, 'document_review':(dm or {}).get('review'),
                'scholarship_material':(dm or {}).get('scholarship_material',False),
                'unit_key':(dm or {}).get('unit_key'), 'unit_name':(dm or {}).get('unit_name')}
        body, heading = word_terms(content), word_terms(title)
        entries.append(Entry(SearchHit(cid,did,content,title,url,0,0,'corpus_bm25',meta=meta),body,heading))
        frequencies.update(set(body)|set(heading))
    data=Catalog(entries,frequencies,sum(sum(e.body.values()) for e in entries)/max(1,len(entries)))
    _CATALOG_CACHE[key]=(stamp,time.monotonic(),data)
    _CATALOG_CACHE.move_to_end(key)
    while len(_CATALOG_CACHE)>8:
        _CATALOG_CACHE.popitem(last=False)
    return data


async def multi_query_search(db, embedder, settings, query, profile, *, unit_keys=(), include_test=False, candidate=False, top_k=4):
    started = time.perf_counter()
    source_format=requested_source_format(query)
    data = await lexical_catalog(db,unit_keys=unit_keys,include_test=include_test,document_format=source_format)
    variants, corrections = query_plan(query, data.frequencies,unit_keys)
    lists, similarities, lexical_ids = [], {}, set()
    diag = {'strategy':'multi-query-v2','source_format':source_format,'query_variants':variants,'query_corrections':corrections,
            'reranker':'not_used','timings_ms':{},'errors':{}, 'unit_keys':list(unit_keys)}
    diag['timings_ms']['catalog'] = round((time.perf_counter()-started)*1000,2)
    if source_format and not data.entries:
        return [],diag
    document_filter={'document_ids':tuple({e.hit.document_id for e in data.entries})} if source_format else {}
    before = time.perf_counter()
    dense_success = lexical_success = False
    for index, variant in enumerate(variants):
        terms = word_terms(variant)
        ranked = score_catalog(data, terms)
        lexical = [replace(e.hit, raw_score=score, rank=i+1) for i,(e,score,matched) in enumerate(ranked[:settings.top_k_bm25]) if matched]
        lexical_ids.update(h.chunk_id for h in lexical)
        lists.append(lexical)
        lexical_success = True
        # All calls on a shared AsyncSession deliberately remain sequential.
        try:
            fts = await bm25_search(db, variant, limit=settings.top_k_bm25, unit_keys=unit_keys,include_test=include_test,**document_filter)
            lists.append(fts)
            lexical_ids.update(h.chunk_id for h in fts)
        except Exception as exc:
            # A SQL error aborts the transaction. Do not continue on that session
            # or turn a database failure into a successful empty search.
            raise RuntimeError('FTS unavailable') from exc
        try:
            vector = await embedder.embed_one(prepare(variant,profile,'query'))
        except Exception as exc:
            diag['errors']['embedding_'+str(index)] = type(exc).__name__
            continue
        dense = await vector_search(db, vector, limit=settings.top_k_vector,unit_keys=unit_keys,
                                    include_test=include_test,candidate=candidate,**document_filter)
        dense_success = True
        for hit in dense:
            similarities[hit.chunk_id] = max(similarities.get(hit.chunk_id,0),hit.raw_score)
        lists.append(dense)
    diag['timings_ms']['retrieval'] = round((time.perf_counter()-before)*1000,2)
    candidates = reciprocal_rank_fusion(lists,k=settings.rrf_k,top_k=60)
    accepted = [h for h in candidates if not period_exclusion(query,h.meta) and
                (h.chunk_id in lexical_ids or similarities.get(h.chunk_id,0)>=float(profile.get('fallback_vector_threshold',.8)))]
    if getattr(settings,'multi_query_rerank',False):
        from src.core.rag.reranker import rerank
        before = time.perf_counter()
        pool = accepted[:settings.reranker_candidates]
        try:
            scores = await rerank(query,[h.title+'\n'+h.content for h in pool],settings.reranker_directory)
            if len(scores)!=len(pool):
                raise ValueError('Reranker count mismatch')
            for hit,score in zip(pool,scores):
                hit.rerank_score = float(score)
            pool.sort(key=lambda h:h.rerank_score,reverse=True)
            accepted = pool
            diag['reranker'] = 'ready'
        except Exception as exc:
            diag.update(reranker='unavailable',reranker_error=type(exc).__name__)
        diag['timings_ms']['rerank'] = round((time.perf_counter()-before)*1000,2)
    chosen, seen = [], set()
    for hit in accepted:
        normalized = re.sub(r'\s+',' ',hit.content).casefold().strip()
        if normalized in seen:
            continue
        seen.add(normalized)
        chosen.append(replace(hit,rank=len(chosen)+1))
        if len(chosen)>=min(top_k,4):
            break
    ids = {h.chunk_id for h in chosen}
    diag['candidates'] = [{'chunk_id':h.chunk_id,'document_id':h.document_id,'url':h.url,'selected':h.chunk_id in ids,
                           'hybrid_score':h.raw_score,'vector_similarity':similarities.get(h.chunk_id),
                           'reason':'selected' if h.chunk_id in ids else 'period_or_evidence_or_rank'} for h in candidates]
    return chosen, diag
