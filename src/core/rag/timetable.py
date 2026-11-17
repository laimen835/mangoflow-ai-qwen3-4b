"""Fail-closed native timetable extraction. Output requires source-bound review.

Only a recognized grid with explicit day/group/lesson labels is accepted. Split
cells are withheld: their upper/lower position alone is not a calendar anchor.
"""
from __future__ import annotations

import re

VERSION = 'native-grid-v1'
DAYS = ('понедельник', 'вторник', 'среда', 'четверг', 'пятница', 'суббота')
GROUP = re.compile(r'\d{2}\.\d{2}\.\d{2}-\d{3}')


def _lines(page, rect):
    # get_textbox includes intersecting glyphs from neighbouring cells. Assign
    # glyphs by their centres so a tall italic font cannot leak across rows.
    lines = []
    for block in page.get_text('rawdict')['blocks']:
        for line in block.get('lines', []):
            chars = [c for span in line['spans'] for c in span['chars']
                     if rect[0] <= (c['bbox'][0] + c['bbox'][2]) / 2 < rect[2]
                     and rect[1] <= (c['bbox'][1] + c['bbox'][3]) / 2 < rect[3]]
            if chars:
                lines.append((min(c['bbox'][1] for c in chars),
                              min(c['bbox'][0] for c in chars), ''.join(c['c'] for c in chars)))
    return sorted(lines)


def _text(page, rect):
    return re.sub(r'\s+', ' ', ' '.join(t for _, _, t in _lines(page, rect))).strip()


def _day(value):
    value = re.sub('[^а-яё]', '', value.casefold())
    # A glyph exactly on the row boundary can be returned twice.
    matches = [d for d in DAYS if value == d or value == d + d[-1]]
    return matches[0] if len(matches) == 1 else None


def extract_page(page):
    """Return verified-layout candidates and explicit exclusions, never OCR guesses."""
    records, excluded = [], []
    for table in page.find_tables(strategy='lines_strict').tables:
        if table.col_count < 5 or (table.col_count - 2) % 3:
            continue
        rows = table.rows
        groups = {}
        for col in range(2, table.col_count, 3):
            rect = rows[0].cells[col]
            label = re.sub(r'\s+', '', _text(page, rect)) if rect else ''
            if GROUP.fullmatch(label):
                groups[col] = label
        if not groups or len(groups) != (table.col_count - 2) // 3:
            continue
        # Vertical labels can extend slightly outside their printed borders;
        # accept them only when the entire intersecting text is a known day.
        day_cells = [(r.cells[0], _day(page.get_textbox(r.cells[0])))
                     for r in rows[1:] if r.cells[0]]
        if not day_cells or any(not name for _, name in day_cells):
            excluded.append({'page': page.number + 1, 'reason': 'unrecognized_day_labels'})
            continue
        for row in rows[1:]:
            number_rect = row.cells[1]
            if not number_rect:
                continue
            label = _text(page, number_rect)
            if not re.fullmatch('[1-9]', label):
                excluded.append({'page': page.number + 1, 'reason': 'invalid_lesson_number'})
                continue
            y0, y1 = number_rect[1], number_rect[3]
            day_matches = [name for rect, name in day_cells
                           if rect[1] <= y0 + 1 and rect[3] >= y1 - 1]
            if len(day_matches) != 1:
                continue
            for col, group in groups.items():
                cells = row.cells[col:col + 3]
                base = {'group': group, 'day': day_matches[0], 'lesson': int(label),
                        'page': page.number + 1}
                if any(not cell or abs(cell[1] - y0) > 1 or abs(cell[3] - y1) > 1
                       for cell in cells):
                    excluded.append({**base, 'reason': 'split_or_merged_lesson_requires_review'})
                    continue
                values = [_text(page, cell) for cell in cells]
                # Some PDFs draw the half-week separator as text underlining,
                # which find_tables does not recognize as a cell boundary.
                subject_lines = _lines(page, cells[0])
                baselines = {round(y / 2) for y, _, text in subject_lines if text.strip()}
                if len(baselines) > 1:
                    excluded.append({**base, 'reason': 'multiline_subject_requires_review'})
                    continue
                if not any(values):
                    # An empty cell is not proof that the lesson was cancelled.
                    continue
                if not all(values):
                    excluded.append({**base, 'reason': 'incomplete_lesson_cells'})
                    continue
                records.append({**base, 'subject': values[0], 'room': values[1],
                                'teacher': values[2], 'bbox': [cells[0][0], y0, cells[-1][2], y1],
                                'extraction_version': VERSION, 'reviewed': False})
    return {'records': records, 'excluded': excluded}
