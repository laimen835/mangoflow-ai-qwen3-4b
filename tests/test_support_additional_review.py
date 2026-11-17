import hashlib
import pytest
from scripts.ingest_reviewed_support_quality90 import merge_reviewed_blocks
from src.core.rag.structured_documents import Block


def inputs():
    item = {'sha256': 'source-hash', 'url': 'https://ncsa.ru/source.pdf'}
    old = [Block('Студенты СПК СКГА.', 'Термины', page=3)]
    extraction = {'blocks': [{'page': 3}, {'page': 6}]}
    review = {'text_verified': True, 'scope': 'spo_confirmed', 'scope_quote': old[0].text,
              'currency': 'unknown', 'document_period': '2025'}
    text = 'Обучающиеся, имеющие взыскания, к поощрениям не представляются.'
    addition = {**review, 'version': 'visual-transcription-v1', 'source_sha256': item['sha256'],
                'source_url': item['url'], 'blocks': [{'page': 6, 'section': '5.6. Поощрения',
                'text': text, 'text_sha256': hashlib.sha256(text.encode()).hexdigest()}]}
    return item, old, extraction, review, addition


def test_review_appends_only_approved_text_without_changing_original_blocks():
    values = inputs()
    blocks = merge_reviewed_blocks(*values)
    assert len(blocks) == 2 and blocks[0] == values[1][0] and len(values[1]) == 1
    assert blocks[1].page == 6 and blocks[1].text == values[-1]['blocks'][0]['text']


@pytest.mark.parametrize('key,value', [('source_sha256', 'other-source'),
    ('source_url', 'https://ncsa.ru/other.pdf'), ('scope', 'unknown'),
    ('currency', 'verified_current'), ('document_period', '2030'), ('scope_quote', 'unverified')])
def test_an_append_cannot_change_applicability_currency_or_source(key, value):
    values = inputs(); values[-1][key] = value
    with pytest.raises(ValueError, match='source and scope'):
        merge_reviewed_blocks(*values)


def test_changed_transcription_missing_page_and_duplicate_clause_are_rejected():
    values = inputs(); values[-1]['blocks'][0]['text'] += ' invented amount'
    with pytest.raises(ValueError, match='invalid reviewed clause'):
        merge_reviewed_blocks(*values)
    values = inputs(); values[-1]['blocks'][0]['page'] = 99
    with pytest.raises(ValueError, match='invalid reviewed clause'):
        merge_reviewed_blocks(*values)
    values = inputs(); values[-1]['blocks'] *= 2
    with pytest.raises(ValueError, match='invalid reviewed clause'):
        merge_reviewed_blocks(*values)
