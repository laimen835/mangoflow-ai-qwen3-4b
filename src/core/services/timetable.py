"""Exact answers from reviewed cells, with the source period always visible."""
from __future__ import annotations
import re
from src.core.rag.timetable import DAYS, GROUP, VERSION


def timetable_request(message, selected_group=None):
    text = message.casefold().replace('ё', 'е')
    schedule_intent = bool(re.search(r'пар[ауы]?\b|заняти|расписан|аудитор|предмет|урок', text))
    short_day_followup = (selected_group and len(text.split()) <= 5
                          and re.match(r'^(?:а\s+)?(?:в|во|на)\s', text))
    if not schedule_intent and not short_day_followup:
        return None
    groups = set(GROUP.findall(message))
    if len(groups) > 1:
        return None
    group = next(iter(groups), selected_group)
    stems = ('понедельник', 'вторник', 'сред', 'четверг', 'пятниц', 'суббот')
    days = [day for day, stem in zip(DAYS, stems) if re.search(r'\b' + stem, text)]
    if not group or len(days) != 1 or re.search(r'\bне\s+в\b', text):
        return None
    return group, days[0]


def reviewed_timetable_answer(doc, message, group, day):
    review = (doc.meta or {}).get('reviewed_timetable', {})
    if (review.get('version') != VERSION or review.get('text_verified') is not True
            or review.get('content_hash') != doc.content_hash
            or not review.get('source_sha256') or not review.get('period')):
        return None
    rows = [r for r in review.get('records', []) if r.get('group') == group and r.get('day') == day]
    if not rows:
        return None
    years = set(re.findall(r'\b20\d{2}\b', message))
    if years - set(re.findall(r'20\d{2}', review['period'])) or re.search(
            r'(?:втор\w*|2(?:-й|ой)?|II)\s+семестр', message, re.I):
        return f"Для запрошенного периода проверенных строк нет. Есть расписание за {review['period']} [1]."
    rows.sort(key=lambda r: r['lesson'])
    answer = f"Группа {group}, {day}. В опубликованном расписании за {review['period']} [1]:\n"
    answer += '\n'.join(f"{r['lesson']}-я пара — {r['subject']}; аудитория {r['room']}; {r['teacher'].rstrip('.')}."
                        for r in rows)
    pages = ', '.join(str(p) for p in sorted({r['page'] for r in rows}))
    answer += f"\nСтраница PDF: {pages}. Обозначения предметов сохранены как в источнике."
    gaps = [r for r in review.get('excluded', []) if r.get('group') == group and r.get('day') == day]
    if gaps:
        answer += '\nЭто неполный список: разделённые ячейки требуют проверки; числитель/знаменатель не определяю.'
    answer += '\nВремя звонков и оперативные замены этим документом не подтверждены.'
    return answer
