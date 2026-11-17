import pytest
from src.core.rag.vector_search import SearchHit
from src.core.rag.legacy_retriever import HybridRetriever
from src.core.rag.prompt import build_context


def hit(number,text):
    return SearchHit(number,number,text,'СПК','https://ncsa.ru/',number,0.8,'vector')


@pytest.mark.asyncio
async def test_focus_keeps_scholarship_source_and_removes_unrelated(monkeypatch,settings):
    rows=[hit(1,'Государственная и социальная стипендии.'),hit(2,'Официальная страница колледжа и виды обучения')]
    async def search(**kwargs):
        assert kwargs['include_test'] is False
        return rows
    monkeypatch.setattr('src.core.rag.legacy_retriever.vector_search',search)
    monkeypatch.setattr('src.core.rag.legacy_retriever.bm25_search',search)
    class Embedder:
        async def embed_one(self,q):return [0.0]
    result=await HybridRetriever(None,Embedder(),settings).search('Какие виды стипендии упоминаются на странице колледжа?')
    assert [h.chunk_id for h in result] == [1]


@pytest.mark.asyncio
async def test_out_of_scope_semantic_similarity_is_not_evidence(monkeypatch,settings):
    async def search(**kwargs):return [hit(1,'Приёмная комиссия колледжа')]
    monkeypatch.setattr('src.core.rag.legacy_retriever.vector_search',search)
    monkeypatch.setattr('src.core.rag.legacy_retriever.bm25_search',search)
    class Embedder:
        async def embed_one(self,q):return [0.0]
    assert await HybridRetriever(None,Embedder(),settings).search('Какая погода на Марсе?') == []


def test_context_normalizes_word_per_line_without_changing_facts():
    context=build_context([hit(1,'Государственная\nи\nсоциальная\nстипендии.\n09.02.07-251')])
    assert 'Государственная и социальная стипендии. 09.02.07-251' in context
    assert '[1]' in context and 'https://ncsa.ru/' in context
