"""Restore a reviewed clause before fitting the final prompt, not afterwards."""
from __future__ import annotations
import hashlib
import re
from dataclasses import replace
from functools import lru_cache
from src.core.rag.bm25_search import query_terms


@lru_cache(maxsize=1)
def _morph():
    import pymorphy3
    return pymorphy3.MorphAnalyzer()


def _terms(text):
    return {_morph().parse(t)[0].normal_form for t in query_terms(text)}


def verified_section_text(hit):
    review = hit.meta.get('document_review') or {}
    section = hit.meta.get('section', '')
    text = hit.meta.get('reviewed_section_text')
    expected = review.get('section_hashes', {}).get(section)
    if (review.get('scope') == 'spo_confirmed' and review.get('text_verified') is True
            and isinstance(text, str) and len(text) <= 3500 and expected
            and hashlib.sha256(text.encode()).hexdigest() == expected):
        return text
    return None


def restore_reviewed_sections(hits, query):
    """Keep unreviewed paths unchanged; restore only hash-approved short sections.

    The caller applies the normal context size limit to these *whole* clauses.
    Source IDs stay attached to the real retrieved chunk; sibling clauses are
    not invented or selected from titles alone.
    """
    if not any(h.meta.get('reviewed_section_text') for h in hits):
        return hits
    restored = []; seen = set(); terms = _terms(query)
    for order, hit in enumerate(hits):
        section = hit.meta.get('section', '')
        text = verified_section_text(hit)
        if text is None:
            restored.append((0, order, hit)); continue
        key = (hit.document_id, section)
        if key in seen:
            continue
        seen.add(key)
        full = replace(hit, content=f'Раздел: {section}\n{text}',
                       meta={**hit.meta, 'section_context_restored': True})
        # Section headings are a soft ordering signal for otherwise retrieved
        # material. They cannot make an absent document pass retrieval.
        boost = len(terms & _terms(section))
        restored.append((boost, order, full))
    restored.sort(key=lambda entry: (-entry[0], entry[1]))
    return [hit for _, _, hit in restored]
