"""Opt-in pilot: scoped hybrid candidates before word gates, then ONNX reranking."""
import re
import time

from src.core.rag.bm25_search import bm25_search
from src.core.rag.period_policy import period_exclusion
from src.core.rag.query import expand_query, focus_terms
from src.core.rag.reranker import rerank
from src.core.rag.rrf import reciprocal_rank_fusion
from src.core.rag.vector_search import vector_search


async def academy_candidates(db,embedder,settings,query,*,unit_keys=(),include_test=False):
    expanded=expand_query(query)
    diag={'timings_ms':{},'excluded':[],'unit_keys':list(unit_keys),'errors':{}}
    vector=[];lexical=[];focused=[]
    started=time.perf_counter()
    try:
        embedding=await embedder.embed_one(expanded)
        vector=await vector_search(db,embedding,limit=settings.top_k_vector,unit_keys=unit_keys,include_test=include_test)
    except Exception as exc:
        diag['errors']['vector']=type(exc).__name__
    diag['timings_ms']['vector']=round((time.perf_counter()-started)*1000,2)
    started=time.perf_counter()
    try:
        lexical=await bm25_search(db,query=expanded,limit=settings.top_k_bm25,unit_keys=unit_keys,include_test=include_test)
        focus=focus_terms(query)
        if focus:
            focused=await bm25_search(db,query=' '.join(focus),limit=settings.top_k_bm25,unit_keys=unit_keys,include_test=include_test)
    except Exception as exc:
        diag['errors']['lexical']=type(exc).__name__
    diag['timings_ms']['lexical']=round((time.perf_counter()-started)*1000,2)
    if len(diag['errors'])==2:
        raise RuntimeError('Both search channels unavailable')
    hits=reciprocal_rank_fusion([vector,lexical,focused],k=settings.rrf_k,top_k=60)
    seen=set();selected=[]
    for hit in hits:
        reason=period_exclusion(query,hit.meta)
        text=re.sub(r'\s+',' ',hit.content).strip().casefold()
        if reason or text in seen:
            diag['excluded'].append({'chunk_id':hit.chunk_id,'reason':reason or 'duplicate'})
            continue
        seen.add(text);selected.append(hit)
        if len(selected)>=min(settings.reranker_candidates,20):
            break
    diag['counts']={'vector':len(vector),'lexical':len(lexical),'focused':len(focused),'rerank_candidates':len(selected)}
    return selected,diag


async def academy_search(db,embedder,settings,query,*,unit_keys=(),include_test=False,top_k=4):
    hits,diag=await academy_candidates(db,embedder,settings,query,unit_keys=unit_keys,include_test=include_test)
    started=time.perf_counter()
    try:
        scores=await rerank(query,[h.title+'\n'+h.content for h in hits],settings.academy_reranker_directory)
        if len(scores)!=len(hits):
            raise ValueError('Invalid score count')
        for hit,score in zip(hits,scores):
            hit.rerank_score=float(score)
        hits.sort(key=lambda h:h.rerank_score,reverse=True)
        diag['reranker']='ready'
    except Exception as exc:
        diag.update(reranker='unavailable',reranker_error=type(exc).__name__)
    diag['timings_ms']['rerank']=round((time.perf_counter()-started)*1000,2)
    chosen=hits[:min(top_k,4)]
    chosen_ids={h.chunk_id for h in chosen}
    diag['candidates']=[{'chunk_id':h.chunk_id,'document_id':h.document_id,'url':h.url,
                         'hybrid_score':h.raw_score,'rerank_score':h.rerank_score,'selected':h.chunk_id in chosen_ids,
                         'reason':'selected' if h.chunk_id in chosen_ids else 'rank_limit'} for h in hits]
    for rank,hit in enumerate(chosen,1):
        hit.rank=rank
    return chosen,diag
