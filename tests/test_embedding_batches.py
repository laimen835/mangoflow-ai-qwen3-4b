from types import SimpleNamespace
import pytest
from src.core.rag.embeddings import OpenAIEmbeddingProvider


@pytest.mark.asyncio
async def test_large_document_batches_are_bounded_and_response_order_is_restored():
    calls = []
    async def create(*, model, input):
        calls.append(input)
        return SimpleNamespace(data=[SimpleNamespace(index=i, embedding=[float(value)])
                                     for i, value in reversed(list(enumerate(input)))])
    provider = object.__new__(OpenAIEmbeddingProvider)
    provider._model = 'local-model'
    provider._client = SimpleNamespace(embeddings=SimpleNamespace(create=create))
    vectors = await provider.embed_many([str(i) for i in range(65)])
    assert [len(batch) for batch in calls] == [32, 32, 1]
    assert vectors == [[float(i)] for i in range(65)]
