"""Evaluate candidate behavior on the 20 frozen insufficient-information questions."""
import asyncio,json
from pathlib import Path
from src.core.config import settings
from src.core.db.session import AsyncSessionLocal,engine
from src.core.rag.embeddings import build_embedder
from src.core.rag.retriever import HybridRetriever
from src.core.llm import build_llm
from src.core.services.chat_service import ChatService
from src.core.api.schemas import ChatRequest


async def main():
    data=json.loads(Path('tests/eval/search_cases.json').read_text(encoding='utf-8'))
    results=[]
    llm=build_llm(settings);embedder=build_embedder(settings)
    for case in data['cases']:
        if case['answerable']:continue
        async with AsyncSessionLocal() as db:
            service=ChatService(db,llm,HybridRetriever(db,embedder,settings,index_slot='candidate',strategy='semantic'),settings)
            result=await service.handle(ChatRequest(message=case['question']))
            results.append({'id':case['id'],'question':case['question'],'response':result.model_dump(mode='json')})
            Path('logs/search-negative-candidate.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')
            print(case['id'],result.usage.get('fallback_type',result.usage.get('route')),flush=True)
    await engine.dispose()


if __name__=='__main__':asyncio.run(main())
