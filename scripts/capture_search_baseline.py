import asyncio,json,time
from pathlib import Path
import httpx
from src.core.config import settings
from src.core.db.session import AsyncSessionLocal,engine
from src.core.rag.embeddings import build_embedder
from src.core.rag.legacy_retriever import HybridRetriever
from src.core.rag.query import contextual_query


async def main():
    dataset=json.loads(Path('tests/eval/search_cases.json').read_text(encoding='utf-8'))
    results=[]
    async with AsyncSessionLocal() as db:
        retriever=HybridRetriever(db,build_embedder(settings),settings)
        for case in dataset['cases']:
            hits=await retriever.search(contextual_query(case['question'],case.get('history',[])),top_k=4)
            expected={e['chunk_id'] for e in case['evidence']}
            results.append({'id':case['id'],'split':case['split'],'answerable':case['answerable'],
                'found_evidence':bool(expected.intersection(h.chunk_id for h in hits)),
                'hits':[{'chunk_id':h.chunk_id,'content':h.content,'title':h.title,'url':h.url} for h in hits],
                'diagnostics':retriever.last_diagnostics})
    report={'corpus_sha256':dataset['corpus_sha256'],'results':results,'negative_answers':[]}
    path=Path('logs/search-baseline.json');path.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    async with httpx.AsyncClient(base_url='http://127.0.0.1:8000',timeout=240) as client:
        for case in dataset['cases']:
            if case['answerable']:continue
            r=await client.post('/api/v1/chat',json={'message':case['question']});r.raise_for_status()
            report['negative_answers'].append({'id':case['id'],'question':case['question'],'response':r.json()})
            path.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    for split in ['tune','holdout']:
        rows=[r for r in results if r['split']==split and r['answerable']]
        print(split,sum(r['found_evidence'] for r in rows),'/',len(rows),flush=True)
    await engine.dispose()


if __name__=='__main__':asyncio.run(main())
