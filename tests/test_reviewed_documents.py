import hashlib
import pytest
from types import SimpleNamespace
from src.core.rag import reviewed_documents as module
from src.core.rag.document_policy import exclusion_reasons


def test_cross_page_clause_keeps_conditions_and_stops_before_next_applicant_category(monkeypatch):
    pages = [module.Block('СПК среднего профессионального образования. 21.1. Граждане РФ: паспорт,', page=1),
             module.Block('аттестат, 4 фотографии, кроме подачи через портал. 21.2. Иностранные граждане: иные документы.', page=2)]
    monkeypatch.setattr(module, 'extract', lambda body, suffix: (pages, 'pymupdf'))
    expected = '21.1. Граждане РФ: паспорт, аттестат, 4 фотографии, кроме подачи через портал.'
    review = {'source_sha256': hashlib.sha256(b'pdf').hexdigest(), 'scope': 'spo_confirmed',
              'scope_page': 1, 'scope_quote': 'СПК среднего профессионального образования.',
              'ranges': [{'start_page': 1, 'end_page': 2, 'start': '21.1.', 'end_before': '21.2.',
                          'section': 'Документы граждан РФ',
                          'text_sha256': hashlib.sha256(expected.encode()).hexdigest()}]}
    blocks, _ = module.load_reviewed_pdf(b'pdf', review)
    assert blocks[0].text == expected and blocks[0].page == 1
    assert 'Иностранные' not in blocks[0].text
    with pytest.raises(ValueError, match='hash mismatch'):
        module.load_reviewed_pdf(b'updated-pdf', review)
    review['ranges'][0]['text_sha256'] = 'changed'
    with pytest.raises(ValueError, match='text changed'):
        module.load_reviewed_pdf(b'pdf', review)


def test_scope_and_ocr_are_not_approved_by_a_link_title(monkeypatch):
    monkeypatch.setattr(module, 'extract', lambda body, suffix: ([module.Block('Бакалавриат', page=1)], 'pymupdf'))
    review = {'source_sha256': hashlib.sha256(b'pdf').hexdigest(), 'scope': 'spo_confirmed',
              'scope_page': 1, 'scope_quote': 'СПК', 'ranges': []}
    with pytest.raises(ValueError, match='applicability'):
        module.load_reviewed_pdf(b'pdf', review)
    monkeypatch.setattr(module, 'extract', lambda body, suffix: ([], 'ocr_required'))
    with pytest.raises(ValueError, match='OCR'):
        module.load_reviewed_pdf(b'pdf', review)
    doc = SimpleNamespace(title='Admission', source_type='ncsa_reviewed', meta={'review_required': True})
    assert len(exclusion_reasons(doc)) == 2
