"""Explicitly scoped answers from reviewed academy-wide housing statistics."""
import re
from src.core.rag.reviewed_web_table import digest


def hostel_fact_request(question):
    text = question.casefold().replace('ё', 'е')
    if not re.search(r'общежити|интернат', text): return None
    if re.search(r'положени', text) and re.search(r'где|ссылк|посмотр|скач|найти|открыть', text):
        return 'regulation_link'
    if re.search(r'свободн|вакант|заброни|гарантир|получу|дают ли|заселят|выделен|квот', text): return None
    # SPK-specific counts must not silently become academy-wide statistics.
    if re.search(r'\bспк\b|колледж|институт', text): return None
    fields = []
    if 'площад' in text:
        total = bool(re.search(r'\bобщ(?:ая|ей|ую|ий|его|ем)\b', text))
        living = bool(re.search(r'\bжил(?:ая|ой|ую|ые|ых)\b', text))
        if total and living:
            fields += ['Общая площадь общежитий, интернатов (м кв.)',
                       'Жилая площадь общежитий, интернатов (м кв.)']
        elif living:
            fields.append('Жилая площадь общежитий, интернатов (м кв.)')
        elif total:
            fields.append('Общая площадь общежитий, интернатов (м кв.)')
        else:
            return None  # Do not choose total vs living space for the user.
    if re.search(r'питани|буфет|столов', text):
        fields.append('Наличие питания (включая буфеты, столовые) в общежитиях, в интернатах')
    if re.search(r'инвентар|мебел', text):
        fields.append('Обеспеченность общежитий, интернатов 100% мягким и жестким инвентарем по установленным стандартным нормам')
    if re.search(r'приспособлен|инвалид|ограниченн\w* возможност', text):
        fields.append('Количество жилых помещений в общежитиях/интернатах, приспособленных для использования инвалидами и лицами с ограниченными возможностями здоровья')
    if fields:
        return fields[0] if len(fields) == 1 else fields
    if re.search(r'сколько|количеств|число', text):
        if re.search(r'когда|кем|кому|документ|заявлен', text): return None
        if re.search(r'мест', text): return 'Количество мест в общежитиях, в интернатах'
        return 'Количество общежитий/интернатов'
    return None


def format_hostel_fact(doc, field):
    meta = doc.meta or {}; review = meta.get('review') or {}; facts = meta.get('public_facts')
    if (review.get('scope') != 'academy_general' or review.get('text_verified') is not True
            or not isinstance(facts, dict) or digest(facts) != review.get('facts_sha256')
            or not meta.get('source_sha256')):
        return None
    if field == 'regulation_link':
        links = facts.get('links', [])
        if len(links) != 1: return None
        link = links[0]
        return (f"В официальном разделе академии опубликована ссылка «{link['title']}» [1]:\n{link['url']}\n"
                'Это ссылка на документ; применимость отдельных правил к СПК и их действие на нужную дату проверяются отдельно.',
                link['title'] + ': ' + link['url'])
    rows = []
    for key in field if isinstance(field, list) else [field]:
        matches = [r for r in facts.get('rows', []) if r.get('Наименование показателя') == key]
        if len(matches) != 1 or not all(k in matches[0] for k in ['Общежития', 'Интернаты']): return None
        rows.append(matches[0])
    quote = '\n'.join(' | '.join(f'{k}: {v}' for k, v in row.items()) for row in rows)
    values = '\n'.join(f"{row['Наименование показателя']} — общежития: {row['Общежития']}; интернаты: {row['Интернаты']}." for row in rows)
    return (f"В опубликованной таблице академии [1]:\n{values}\n"
            'Это общие данные СКГА, не квота колледжа и не количество свободных мест. '
            f"Дата загрузки источника: {meta.get('fetched_at', '')[:10]}.", quote)
