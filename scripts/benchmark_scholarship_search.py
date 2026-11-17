"""Warm search timing on identical frozen questions, without generation."""
import asyncio,json,math
from pathlib import Path
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine,async_sessionmaker
from src.core.config import settings
from src.core.rag.retriever import HybridRetriever
from src.core.rag.embeddings import build_embedder
from src.core.rag.query import contextual_query

ROOT=Path('D:/Codex-RAG-runtime/scholarship-pilot')


async def main():
    cases=json.loads(Path('tests/eval/scholarship_cases.json').read_text(encoding='utf-8'))['cases'];results={}
    for variant in ['baseline','legacy','structured']:
        url=make_url(settings.database_url).set(database='rag_scholarship_'+variant).render_as_string(hide_password=False)
        cfg=settings.model_copy(update={'database_url':url});engine=create_async_engine(url)
        sessions=async_sessionmaker(engine);embedder=build_embedder(cfg);timings=[]
        try:
            async with sessions() as db:
                r=HybridRetriever(db,embedder,cfg);await r.retrieve('Материальная помощь студентам СПК')
                for repeat in range(3):
                    for case in cases:
                        history=[{'role':'user','content':case['setup']}] if case.get('setup') else []
                        result=await r.retrieve(contextual_query(case['question'],history))
                        assert result.diagnostics.get('profile')=='legacy-v1' and result.diagnostics.get('reranker')=='not_used'
                        timings.append(result.diagnostics['total_ms'])
            ordered=sorted(timings);results[variant]={'warm_queries':len(timings),'p95_ms':ordered[math.ceil(len(ordered)*.95)-1],'times_ms':timings}
        finally:await engine.dispose()
    (ROOT/'search-timings.json').write_text(json.dumps(results,indent=2),encoding='utf-8')
    print({k:v['p95_ms'] for k,v in results.items()})


if __name__=='__main__':asyncio.run(main())
