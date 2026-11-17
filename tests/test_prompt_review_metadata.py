from src.core.rag.prompt import context_block
from src.core.rag.vector_search import SearchHit


def test_internal_review_status_is_not_supplied_as_document_evidence():
    hit = SearchHit(1, 1, 'Приказ готовит подразделение А.', 'Положение', None, 1, 1., 'vector',
        meta={'scholarship_material': True, 'page': 4, 'document_review': {
            'document_period': '2025 (год на обложке; действующий статус не установлен)',
            'currency': 'unknown', 'scope_quote': 'Студенты СПК.'}})
    context = context_block(hit, 1)
    assert 'Приказ готовит подразделение А.' in context and '2025' in context and 'Страница: 4' in context
    assert 'unknown' not in context and 'Актуальность:' not in context and 'действующий статус не установлен' not in context
    assert 'не объявляя его действующим сейчас' in context
