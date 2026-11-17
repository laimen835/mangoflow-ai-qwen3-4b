from __future__ import annotations
from dataclasses import dataclass

from src.core.config import Settings
from src.core.llm.base import LLMClient
from src.core.rag.prompt import build_messages
from src.core.rag.retriever import HybridRetriever
from src.core.rag.vector_search import SearchHit


@dataclass
class RagAnswer:
    answer: str
    hits: list[SearchHit]
    usage: dict


async def answer_question(question:str,retriever:HybridRetriever,llm:LLMClient,settings:Settings,
                          history:list[dict]|None=None,top_k:int|None=None)->RagAnswer:
    if hasattr(retriever,'retrieve'):
        result=await retriever.retrieve(question,top_k=top_k)
        hits=result.hits
        retrieval_usage=result.usage()
        status=result.status
    else:
        hits=await retriever.search(question,top_k=top_k)
        status='matched' if hits else 'no_match'
        retrieval_usage={'retrieval_status':status}
    if not hits:
        answer=('Сейчас не удаётся выполнить поиск по материалам. Это техническая ошибка; повторите вопрос позже.'
                if status=='unavailable' else 'Я не нашёл достаточного подтверждения в загруженных материалах.')
        return RagAnswer(answer,[],{**retrieval_usage,'model':'no_source','hits':0})
    messages=build_messages(question,hits,history=history)
    response=await llm.generate(messages)
    return RagAnswer(response.text,hits,{**response.usage,**retrieval_usage,'hits':len(hits)})
