from types import SimpleNamespace
import pytest
from src.core.rag import legacy_retriever as module
from src.core.rag.vector_search import SearchHit


@pytest.mark.asyncio
async def test_auxiliary_price_synonym_cannot_exclude_an_answer_about_funding():
    class Embedder:
        async def embed_one(self, text): return [0.1]
    evidence=SearchHit(1,1,'Есть бюджетные места и платные образовательные услуги.',
                       'Правила приёма',None,1,0.9,'vector')
    async def vectors(**kwargs): return [evidence]
    async def lexical(**kwargs): return [evidence]
    monkeypatch=pytest.MonkeyPatch()
    monkeypatch.setattr(module,'vector_search',vectors);monkeypatch.setattr(module,'bm25_search',lexical)
    try:
        settings=SimpleNamespace(top_k_final=4,top_k_vector=20,top_k_bm25=20,rrf_k=60)
        result=await module.HybridRetriever(None,Embedder(),settings).search('Бюджет или платные места?')
        assert [h.chunk_id for h in result]==[1]
    finally:monkeypatch.undo()


@pytest.mark.asyncio
async def test_graduate_question_can_retrieve_requirements_without_the_word_graduate(monkeypatch):
    class Embedder:
        async def embed_one(self, text): return [0.1]
    history=SearchHit(1,1,'Первый выпуск техникума состоялся давно.', 'История',None,1,.9,'vector')
    requirements=SearchHit(2,2,'Документы поступающего: паспорт, документ об образовании, фотографии.',
                           'Правила приема',None,2,.8,'vector')
    async def vectors(**kwargs): return [history, requirements]
    async def lexical(**kwargs): return [requirements]
    monkeypatch.setattr(module,'vector_search',vectors);monkeypatch.setattr(module,'bm25_search',lexical)
    config=SimpleNamespace(top_k_final=4,top_k_vector=20,top_k_bm25=20,rrf_k=60)
    retriever=module.HybridRetriever(None,Embedder(),config)
    results=await retriever.search('Какие документы нужны выпускнику для поступления?')
    assert results[0].chunk_id==2
    row=next(r for r in retriever.last_diagnostics['candidates'] if r['chunk_id']==2)
    assert row['kept'] and row['focus_matches']==0
