"""Development comparison on previously inspected sets, NOT the 90% gate."""
import asyncio,json,time,math
from pathlib import Path
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine,async_sessionmaker
from src.core.config import settings
from src.core.rag.retriever import HybridRetriever
from src.core.rag.embeddings import build_embedder
from src.core.rag.query import contextual_query
from scripts.quality90_workspace import ROOT


async def main():
    cases=json.loads(Path('tests/eval/search_cases.json').read_text(encoding='utf-8'))['cases']
    result={}
    for policy in ['profile','evidence']:
        url=make_url(settings.database_url).set(database='rag_quality90_'+('baseline' if policy=='profile' else 'candidate')).render_as_string(hide_password=False)
        cfg=settings.model_copy(update={'database_url':url,'retrieval_policy':policy})
        engine=create_async_engine(url);sessions=async_sessionmaker(engine);embedder=build_embedder(cfg);rows=[]
        try:
            async with sessions() as db:
                retriever=HybridRetriever(db,embedder,cfg)
                await retriever.retrieve('Прогрев поиска по колледжу')
                for case in cases:
                    response=await retriever.retrieve(contextual_query(case['question'],case.get('history',[])))
                    expected={e['chunk_id'] for e in case['evidence']}
                    context_ids={i for h in response.hits for i in h.meta.get('supporting_chunk_ids',[h.chunk_id])}
                    found=bool(expected&context_ids)
                    rows.append({'id':case['id'],'question':case['question'],'answerable':case['answerable'],'found':found,
                        'status':response.status,'hits':[{'id':h.chunk_id,'url':h.url,'content':h.content} for h in response.hits],
                        'diagnostics':response.diagnostics})
                times=sorted(r['diagnostics']['total_ms'] for r in rows)
                result[policy]={'evidence_found':sum(r['found'] for r in rows),'positive_questions':60,
                    'p95_ms':times[math.ceil(.95*len(times))-1],'rows':rows}
                print(policy,'evidence',result[policy]['evidence_found'],'/60, p95',result[policy]['p95_ms'],flush=True)
        finally:await engine.dispose()
    result['limitation']='Previously inspected development questions; retrieval evidence only, not independent end-to-end correctness.'
    (ROOT/'retrieval-development.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')


if __name__=='__main__':asyncio.run(main())
