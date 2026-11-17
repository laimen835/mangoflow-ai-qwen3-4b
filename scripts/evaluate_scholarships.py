"""Frozen fact-disjoint pilot; run baseline before changing answer behavior."""
import argparse,asyncio,json,re,time
from pathlib import Path
from sqlalchemy import select
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine,async_sessionmaker
from src.core.config import settings
from src.core.rag.embeddings import build_embedder
from src.core.rag.retriever import HybridRetriever
from src.core.rag.prompt import context_hits,build_context
from src.core.services.chat_service import ChatService
from src.core.api.schemas import ChatRequest
from src.core.llm import build_llm
from src.core.db.models import Document

ROOT=Path('D:/Codex-RAG-runtime/scholarship-pilot')
DATASET=Path('tests/eval/scholarship_cases.json')


def normalized(text):return re.sub(r'\s+',' ',text).casefold().replace('ё','е')


async def evaluate(stage,split):
    database={'baseline':'rag_scholarship_baseline','legacy':'rag_scholarship_legacy','structured':'rag_scholarship_structured'}[stage]
    cfg=settings.model_copy(update={'database_url':make_url(settings.database_url).set(database=database).render_as_string(hide_password=False)})
    engine=create_async_engine(cfg.database_url);sessions=async_sessionmaker(engine,expire_on_commit=False)
    embedder=build_embedder(cfg);llm=build_llm(cfg)
    cases=json.loads(DATASET.read_text(encoding='utf-8'))['cases'];out=ROOT/f'{stage}-{split}.json'
    if out.exists():raise ValueError('Evaluation already exists; preserve original control results')
    results=[]
    try:
        for case in cases:
            if split!='all' and case['split']!=split:continue
            async with sessions() as db:
                retriever=HybridRetriever(db,embedder,cfg);service=ChatService(db,llm,retriever,cfg)
                session=None
                if case.get('setup'):
                    response=await service.handle(ChatRequest(message=case['setup']));session=response.session_id
                started=time.perf_counter()
                response=await service.handle(ChatRequest(message=case['question'],session_id=session))
                result=retriever.last_result
                hits=context_hits(result.hits) if result else []
                docs=(await db.scalars(select(Document).where(Document.source_url==case['source']))).all() if case.get('source') else []
                quote=normalized(case.get('quote',''))
                evidence=bool(quote) and quote in normalized(build_context(hits))
                checks=[any(re.search(pattern,response.answer,re.I) for pattern in group) for group in case['answer_patterns']]
                source_ok=not case['answerable'] or any(s.url==case['source'] for s in response.sources)
                row={'id':case['id'],'split':case['split'],'question':case['question'],'answerable':case['answerable'],
                    'auto_passed':all(checks) and source_ok,'needs_factual_review':True,
                    'pipeline':{'document_present':bool(docs),'extracted':bool(docs) and all(d.content_hash for d in docs),
                                'evidence_in_context':evidence,'context':build_context(hits)},
                    'retrieval':result.diagnostics if result else None,'response':response.model_dump(mode='json'),
                    'total_ms':round((time.perf_counter()-started)*1000,2)}
                results.append(row)
                out.write_text(json.dumps({'stage':stage,'split':split,'results':results},ensure_ascii=False,indent=2),encoding='utf-8')
                print(stage,case['id'],'PASS' if row['auto_passed'] else 'REVIEW',flush=True)
    finally:await engine.dispose()


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('stage',choices=['baseline','legacy','structured']);parser.add_argument('--split',choices=['all','tune','holdout'],default='tune');args=parser.parse_args()
    asyncio.run(evaluate(args.stage,args.split))
