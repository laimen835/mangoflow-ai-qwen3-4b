"""Join proven overlaps and respect an explicitly requested web source."""
from dataclasses import replace
from src.core.rag.ncsa_source import SEED
import re


def prepare_source_hits(hits, query):
    remaining = list(hits); result = []
    while remaining:
        anchor = remaining.pop(0); members = [anchor]; content = anchor.content
        low = high = anchor.meta.get('chunk_index')
        if type(low) is int:
            progress = True
            while progress:
                progress = False
                for candidate in list(remaining):
                    index = candidate.meta.get('chunk_index')
                    if candidate.document_id != anchor.document_id or type(index) is not int:
                        continue
                    if index == low - 1:
                        before, after = candidate.content, content
                    elif index == high + 1:
                        before, after = content, candidate.content
                    else:
                        continue
                    overlap = next((n for n in range(min(256, len(before), len(after)), 15, -1)
                                    if before.endswith(after[:n])), 0)
                    if not overlap: continue
                    joined = before + after[overlap:]
                    if len(joined) > 3000: continue
                    content = joined; low = min(low, index); high = max(high, index)
                    members.append(candidate); remaining.remove(candidate); progress = True; break
        if len(members) > 1:
            anchor = replace(anchor, content=content, meta={**anchor.meta,
                'supporting_chunk_ids': [h.chunk_id for h in sorted(members, key=lambda h:h.meta['chunk_index'])],
                'overlap_context_restored': True})
        result.append(anchor)
    explicit_college_page = re.search(
        r'(?:основн\w*|главн\w*)\s+страниц|на\s+страниц[еы]\s+(?:спк|колледжа)', query, re.I)
    if explicit_college_page and re.search(r'\bспк\b|колледж', query, re.I):
        # A question about what the named page says is bounded by that page.
        # Comparison/conflict questions still need the other documents.
        page_hits = [h for h in result if h.url == SEED]
        comparison = re.search(r'сравн|противореч|расхожд|различ|иначе|положени|документ', query, re.I)
        if page_hits and not comparison:
            return page_hits
        result.sort(key=lambda h: h.url != SEED)
    return result
