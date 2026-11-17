"""Source-bound review ranges, including clauses continued on the next page."""
from __future__ import annotations
import hashlib
import re
from src.core.rag.structured_documents import Block, extract

VERSION = 'reviewed-ranges-v1'


def normalize(text):
    return re.sub(r'\s+', ' ', text).strip()


def load_reviewed_pdf(body, review):
    if hashlib.sha256(body).hexdigest() != review['source_sha256']:
        raise ValueError('Review source hash mismatch')
    blocks, method = extract(body, '.pdf')
    if method != 'pymupdf' or not blocks:
        raise ValueError('Native text required; OCR requires a separate review')
    pages = {b.page: normalize(b.text) for b in blocks}
    if review.get('period_basis'):
        period = re.fullmatch(r'((?:19|20)\d{2})/((?:19|20)\d{2}) учебный год', review.get('period', ''))
        period_quote = normalize(review.get('period_quote', ''))
        if (review['period_basis'] != 'academic_cycle' or not period
                or int(period[2]) != int(period[1]) + 1 or not period_quote
                or period[0] not in period_quote
                or period_quote not in pages.get(review.get('period_page'), '')):
            raise ValueError('Academic period is not supported by the source')
    proof = normalize(review['scope_quote'])
    if (review.get('scope') != 'spo_confirmed' or not proof
            or proof not in pages.get(review['scope_page'], '')
            or not re.search(r'среднего профессионального|СПК', proof)):
        raise ValueError('SPK applicability is not supported by the source')
    result = []
    for span in review['ranges']:
        page_range = range(span['start_page'], span['end_page'] + 1)
        joined = ' '.join(pages[p] for p in page_range)
        start = joined.index(normalize(span['start']))
        end = joined.index(normalize(span['end_before']), start + len(normalize(span['start'])))
        text = joined[start:end].strip()
        if hashlib.sha256(text.encode()).hexdigest() != span['text_sha256']:
            raise ValueError('Reviewed text changed')
        result.append(Block(text, span['section'], page=span['start_page']))
    if not result:
        raise ValueError('No approved ranges')
    return result, method
