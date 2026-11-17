"""Post-review diagnostic: actual two-turn conversations for frozen cases 55-58."""
import asyncio
import hashlib
import json
import time
from pathlib import Path
from uuid import uuid4

import httpx
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from src.core.config import settings
from src.core.api.schemas import ChatRequest
from src.core.services.chat_service import ChatService
from src.core.rag.retriever import HybridRetriever
from src.core.rag.embeddings import build_embedder
from src.core.llm.ollama_client import OllamaClient
from scripts.evaluate_deep import corpus_stamp
from run_applicants import MODELS, Recorded, ROOT, save


async def main():
    out=ROOT/'real-dialogues-diagnostic.json';assert not out.exists()
    url=make_url(settings.database_url)
    assert url.database=='rag_admissions_eval_20261117' and url.port==55433
    engine=create_async_engine(url);sessions=async_sessionmaker(engine,expire_on_commit=False)
    cases_path=ROOT/'cases-frozen.json'
    assert hashlib.sha256(cases_path.read_bytes()).hexdigest()==(ROOT/'cases-sha256.txt').read_text()
    cases=[c for c in json.loads(cases_path.read_text(encoding='utf-8'))['cases'] if c['category']=='dialogue']
    assert len(cases)==4
    report={'status':'running','kind':'post-review diagnostic, not a fresh control or a replacement for the frozen 60-case results','rows':[]}
    try:
        async with sessions() as db:report['corpus_before']=await corpus_stamp(db)
        for label,(port,model) in MODELS.items():
            other_port,other_model=MODELS['9b' if label=='4b' else '4b']
            async with httpx.AsyncClient(timeout=60,trust_env=False) as client:
                r=await client.post(f'http://127.0.0.1:{other_port}/api/generate',json={'model':other_model,'stream':False,'keep_alive':0});r.raise_for_status()
            cfg=settings.model_copy(update={'llm_provider':'ollama','llm_model':model,
                'ollama_base_url':f'http://127.0.0.1:{port}','ollama_think':False,
                'openai_base_url':'http://127.0.0.1:11534/v1','ollama_num_ctx':4096,
                'llm_timeout_s':180,'retrieval_policy':'multi_query','multi_query_rerank':True,
                'auto_sync_enabled':False,'deep_crawl_enabled':False,'answer_mode':'source_quotes'})
            embedder=build_embedder(cfg);llm=Recorded(OllamaClient(cfg))
            for case in cases:
                session_id=uuid4();record={'id':case['id'],'model':label,'turns':[]}
                for question in (case['history'][0]['content'],case['question']):
                    llm.calls=[];start=time.perf_counter()
                    async with sessions() as db:
                        service=ChatService(db,llm,HybridRetriever(db,embedder,cfg),cfg)
                        result=await service.handle(ChatRequest(session_id=session_id,message=question))
                        record['turns'].append({'question':question,'response':result.model_dump(mode='json'),
                            'calls':llm.calls,'wall_ms':round((time.perf_counter()-start)*1000,2)})
                report['rows'].append(record);save(out,report)
                print(label,case['id'],'completed actual two-turn conversation',flush=True)
        async with sessions() as db:report['corpus_after']=await corpus_stamp(db)
        assert report['corpus_before']==report['corpus_after']
        report['status']='complete';save(out,report)
    finally:await engine.dispose()


if __name__=='__main__':asyncio.run(main())
