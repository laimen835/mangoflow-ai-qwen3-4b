import hashlib
from types import SimpleNamespace
import pytest
from src.core.rag.reviewed_web_table import digest, extract_reviewed_table
from src.core.services.hostel_facts import hostel_fact_request, format_hostel_fact


def fixture():
    rows = [['Наименование показателя', 'Общежития', 'Интернаты'],
            ['Количество мест в общежитиях, в интернатах', '42', '0']]
    body = ('<main><table><tr><th>Наименование показателя</th><th>Общежития</th><th>Интернаты</th></tr>'
            '<tr><td>Количество мест в общежитиях, в интернатах</td><td>42</td><td>0</td></tr></table></main>').encode()
    review = {'source_sha256': hashlib.sha256(body).hexdigest(), 'table_sha256': digest(rows),
              'headers': rows[0], 'approved_rows': [rows[1][0]], 'source_url': 'https://ncsa.ru/sveden/grants/'}
    return body, review


def test_table_values_stay_bound_to_their_column_and_snapshot():
    body, review = fixture()
    facts = extract_reviewed_table(body, review)
    assert facts['rows'][0]['Общежития'] == '42' and facts['rows'][0]['Интернаты'] == '0'
    with pytest.raises(ValueError, match='HTML hash'):
        extract_reviewed_table(body.replace(b'42', b'24'), review)
    changed = body.replace(b'42', b'24'); review['source_sha256'] = hashlib.sha256(changed).hexdigest()
    with pytest.raises(ValueError, match='table changed'):
        extract_reviewed_table(changed, review)


def test_academy_totals_are_not_college_quota_or_vacancies():
    body, review = fixture(); facts = extract_reviewed_table(body, review)
    field = hostel_fact_request('Сколько всего мест указано в общежитиях академии?')
    doc = SimpleNamespace(meta={'source_sha256': review['source_sha256'], 'fetched_at': '2026-11-10',
        'review': {'scope': 'academy_general', 'text_verified': True, 'facts_sha256': digest(facts)}, 'public_facts': facts})
    answer, quote = format_hostel_fact(doc, field)
    assert '42' in answer and 'не квота колледжа' in answer and '42' in quote
    assert hostel_fact_request('Сколько свободных мест в общежитии?') is None
    assert hostel_fact_request('Сколько мест выделено для СПК?') is None
    assert hostel_fact_request('Сколько мест в общежитии колледжа?') is None
    facts['rows'][0]['Общежития'] = '999'
    assert format_hostel_fact(doc, field) is None


def test_link_destination_must_match_the_official_source_and_stays_navigation_only():
    body, review = fixture()
    review['approved_links'] = [{'title': 'Положение', 'url': 'https://ncsa.ru/file.pdf'}]
    with pytest.raises(ValueError, match='link'):
        extract_reviewed_table(body, review)
    assert hostel_fact_request('Где опубликовано положение о студенческом общежитии?') == 'regulation_link'
    assert hostel_fact_request('Какие обязанности по положению общежития?') is None
    assert hostel_fact_request('Кому подавать заявление по опубликованному положению общежития?') is None


def test_compound_area_and_food_queries_preserve_reviewed_fields():
    fields = hostel_fact_request('Какова общая и жилая площадь общежитий академии?')
    assert len(fields) == 2 and fields[0].startswith('Общая') and fields[1].startswith('Жилая')
    assert hostel_fact_request('Есть ли питание в общежитиях академии?').startswith('Наличие питания')
    assert hostel_fact_request('Какова площадь общежитий?') is None
    assert hostel_fact_request('Есть ли питание в общежитии СПК?') is None
    rows = [{'Наименование показателя': field, 'Общежития': str(index + 17), 'Интернаты': '0'}
            for index, field in enumerate(fields)]
    facts = {'rows': rows}
    doc = SimpleNamespace(meta={'source_sha256': 'hash', 'fetched_at': '2026-11-10',
        'review': {'scope': 'academy_general', 'text_verified': True, 'facts_sha256': digest(facts)},
        'public_facts': facts})
    answer, quote = format_hostel_fact(doc, fields)
    assert '17' in answer and '18' in answer and 'не квота колледжа' in answer
    assert all(field in quote for field in fields)
