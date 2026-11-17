import pytest
from src.core.rag.evidence_selection import render_selection, select_evidence
from src.core.rag.vector_search import SearchHit
from src.core.llm.base import LLMResponse


def hits():
    return [SearchHit(1, 1, '4 фотографии, кроме случаев подачи через портал.', 'Правила', None, 1, 1, 'vector')]


def test_model_selects_source_text_but_cannot_inject_its_own_unsupported_answer():
    text, status = render_selection({'sources': [1], 'complete': True}, hits())
    assert '4 фотографии, кроме случаев подачи через портал. [1]' in text
    assert status == 'complete_selection'
    with pytest.raises(ValueError):
        render_selection({'sources': [1], 'complete': True, 'answer': 'Диплом и 12 фотографий'}, hits())


@pytest.mark.parametrize('ids', [[2], [0], [-1], [True], [1, 1], ['1']])
def test_reference_validation_rejects_invalid_or_duplicate_source_ids(ids):
    with pytest.raises(ValueError): render_selection({'sources': ids, 'complete': True}, hits())


def test_partial_evidence_and_absence_are_not_labelled_as_full_coverage():
    assert render_selection({'sources': [], 'complete': True}, hits()) == (None, 'no_evidence')
    text, status = render_selection({'sources': [1], 'complete': False}, hits())
    assert status == 'partial_selection' and 'частичное подтверждение' in text


def test_selected_sources_are_renumbered_in_the_same_order_as_returned_context():
    evidence = hits() + [SearchHit(2, 2, 'Другой проверенный факт.', 'Документ 2', None, 2, 1, 'vector')]
    text, _ = render_selection({'sources': [2, 1], 'complete': True}, evidence)
    assert 'Другой проверенный факт. [1]' in text
    assert '4 фотографии, кроме случаев подачи через портал. [2]' in text


@pytest.mark.asyncio
async def test_bad_json_or_unavailable_provider_does_not_expose_an_unchecked_draft():
    class LLM:
        async def generate_structured(self, *args, **kwargs): return LLMResponse(text='{broken', usage={})
    result = await select_evidence(LLM(), 'Документы?', hits())
    assert result.status == 'unavailable' and result.text is None
