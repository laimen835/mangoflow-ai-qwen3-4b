"""Compare a requested year only with a source-verified academic cycle.

A cover/publication date is deliberately insufficient: a 2025 provision can
remain applicable in 2026. Download dates never establish a validity period.
"""
import re


def period_exclusion(question, meta):
    years = {int(y) for y in re.findall(r'\b(?:19|20)\d{2}\b', question)}
    review = meta.get('document_review') or {}
    if (not years or review.get('text_verified') is not True
            or review.get('period_basis') != 'academic_cycle'):
        return None
    match = re.fullmatch(r'((?:19|20)\d{2})/((?:19|20)\d{2}) учебный год', review.get('period', ''))
    if not match or int(match[2]) != int(match[1]) + 1:
        return None
    return 'period_mismatch' if years.isdisjoint({int(match[1]), int(match[2])}) else None
