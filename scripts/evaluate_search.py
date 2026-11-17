"""Tune only on the pre-labelled tuning split, then evaluate held-out queries."""
import asyncio
import json
from pathlib import Path
import statistics
from src.core.config import settings
from src.core.db.session import AsyncSessionLocal,engine
from src.core.rag.embeddings import build_embedder
from src.core.rag.profiles import state_for
from src.core.rag.query import contextual_query
from src.core.rag.retriever import HybridRetriever
from src.core.rag.reranker import rerank
from scripts.search_index import corpus_stamp


def score(case,result,threshold):
    expected={e['chunk_id'] for e in case['evidence']}
    selected=[h['chunk_id'] for h in result['hits'] if h['rerank_score'] is not None and h['rerank_score']>=threshold]
    return bool(expected.intersection(selected)),bool(selected)


async def main():
    dataset=json.loads(Path('tests/eval/search_cases.json').read_text(encoding='utf-8'))
    baseline=json.loads(Path('logs/search-baseline.json').read_text(encoding='utf-8'))
    assert baseline['corpus_sha256']==dataset['corpus_sha256']
    await rerank('Прогрев поиска',['Пример документа для прогрева.'],settings.reranker_directory)
    results=[]
    async with AsyncSessionLocal() as db:
        retriever=HybridRetriever(db,build_embedder(settings),settings,index_slot='candidate',strategy='semantic')
        # Gather scores from tuning questions only before setting a cutoff.
        async def evaluate(case):
            result=await retriever.retrieve(contextual_query(case['question'],case.get('history',[])))
            if result.diagnostics.get('reranker')!='ready':raise RuntimeError(result.diagnostics)
            return {'id':case['id'],'split':case['split'],'status':result.status,
                'hits':[{'chunk_id':h.chunk_id,'rerank_score':h.rerank_score,'content':h.content,'title':h.title,'url':h.url} for h in result.hits],
                'diagnostics':result.diagnostics}
        tune=[c for c in dataset['cases'] if c['split']=='tune']
        for case in tune:results.append(await evaluate(case))
        pairs=list(zip(tune,results))
        out_scores=[h['rerank_score'] for c,r in pairs if c['family']=='out_of_scope' for h in r['hits']]
        threshold=max(out_scores,default=-8)+0.25
        train_found=sum(score(c,r,threshold)[0] for c,r in pairs if c['answerable'])
        state=await state_for(db,settings,for_update=True)
        state.candidate_profile={**state.candidate_profile,'rerank_threshold':threshold}
        await db.commit()
        reviewed_profile=dict(state.candidate_profile)
        reviewed_corpus=await corpus_stamp(db)
        print('Frozen threshold from tuning only:',round(threshold,3),'tune recall',train_found,'/30',flush=True)
        for case in dataset['cases']:
            if case['split']=='holdout':results.append(await evaluate(case))
    results.sort(key=lambda r:r['id'])
    lookup={r['id']:r for r in results}
    before={r['id']:r for r in baseline['results']}
    summary={}
    for split in ['tune','holdout']:
        cases=[c for c in dataset['cases'] if c['split']==split and c['answerable']]
        new=sum(score(c,lookup[c['id']],threshold)[0] for c in cases)
        old=sum(before[c['id']]['found_evidence'] for c in cases)
        summary[split]={'before_found':old,'after_found':new,'answerable':len(cases),'gain_pp':round((new-old)/len(cases)*100,2)}
    timings=sorted(r['diagnostics']['total_ms'] for r in results)
    p95=timings[max(0,int(len(timings)*0.95)-1)]
    report={'corpus_sha256':dataset['corpus_sha256'],'threshold':threshold,'summary':summary,
        'p95_search_ms':p95,'median_search_ms':statistics.median(timings),'results':results,
        'retrieval_gate_passed':summary['holdout']['gain_pp']>=10 and p95<=3000,
        'activation_approved':False,'reviewed_candidate_profile':reviewed_profile,'reviewed_corpus_stamp':reviewed_corpus,
        'activation':'Pending negative-answer factual review and regression checks.'}
    Path('logs/search-comparison.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k!='results'},ensure_ascii=False,indent=2),flush=True)
    await engine.dispose()


if __name__=='__main__':asyncio.run(main())
