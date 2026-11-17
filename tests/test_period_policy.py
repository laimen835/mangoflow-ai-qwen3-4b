import hashlib
import pytest
from src.core.rag.period_policy import period_exclusion
from src.core.rag import reviewed_documents as reviewed


def meta(**overrides):
    return {'document_review': {'text_verified': True, 'period_basis': 'academic_cycle',
        'period': '2026/2027 учебный год', **overrides}}


def test_verified_cycle_filters_other_year_without_using_publication_date():
    assert period_exclusion('По положению 2025 года кто принимает решение?', meta()) == 'period_mismatch'
    for query in ['Приём в 2026 году', 'Правила 2027 года', 'Сравни правила 2025 и 2026 годов', 'Срок подачи заявления']:
        assert period_exclusion(query, meta()) is None
    assert period_exclusion('Какая помощь в 2026 году?', {'document_review': {
        'text_verified': True, 'currency': 'unknown', 'document_period': '2025'}}) is None
    assert period_exclusion('2025 год', meta(text_verified=False)) is None
    assert period_exclusion('2025 год', meta(period='2026/2030 учебный год')) is None


def test_period_must_be_read_from_same_verified_pdf(monkeypatch):
    body=b'pdf'
    text='СПК среднего профессионального образования. на 2026/2027 учебный год. 1. Текст. 2. Далее.'
    monkeypatch.setattr(reviewed, 'extract', lambda *args: ([reviewed.Block(text, page=1)], 'pymupdf'))
    review={'source_sha256':hashlib.sha256(body).hexdigest(), 'scope':'spo_confirmed',
        'scope_page':1,'scope_quote':'СПК среднего профессионального образования.',
        'period':'2026/2027 учебный год','period_basis':'academic_cycle','period_page':1,
        'period_quote':'на 2026/2027 учебный год','ranges':[{'start_page':1,'end_page':1,
            'start':'1. Текст.','end_before':'2. Далее.','section':'Пункт 1',
            'text_sha256':hashlib.sha256('1. Текст.'.encode()).hexdigest()}]}
    assert reviewed.load_reviewed_pdf(body, review)[0][0].text=='1. Текст.'
    for bad in [{'period_quote':'на 2030/2031 учебный год'}, {'period_page':2},
                {'period':'2030/2031 учебный год'}, {'period_basis':'download_date'}]:
        with pytest.raises(ValueError, match='Academic period'):
            reviewed.load_reviewed_pdf(body, {**review, **bad})
