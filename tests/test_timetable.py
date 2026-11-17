from types import SimpleNamespace
from src.core.rag.timetable import extract_page, VERSION
from src.core.services.timetable import timetable_request, reviewed_timetable_answer


def test_group_and_day_do_not_bleed_between_queries():
    assert timetable_request('Какие пары у 09.02.07-251 в среду?') == ('09.02.07-251', 'среда')
    assert timetable_request('А во вторник?', '09.02.07-252') == ('09.02.07-252', 'вторник')
    assert timetable_request('В понедельник и вторник у 09.02.07-251?') is None
    assert timetable_request('Не в понедельник у 09.02.07-251?') is None
    assert timetable_request('09.02.07-251 и 09.02.07-252 в среду') is None
    assert timetable_request('Какие документы принести в среду?', '09.02.07-251') is None


def document():
    return SimpleNamespace(content_hash='current', meta={'reviewed_timetable': {
        'version': VERSION, 'text_verified': True, 'source_sha256': 'reviewed-file',
        'content_hash': 'current', 'period': 'I семестр 2026–2027 учебного года',
        'records': [{'group': '09.02.07-251', 'day': 'среда', 'lesson': 1, 'page': 2,
                     'subject': 'ЭВМ', 'room': '403м', 'teacher': 'Узденова К.М.'}],
        'excluded': []}})


def test_review_is_bound_to_content_period_and_exact_group():
    doc = document()
    answer = reviewed_timetable_answer(doc, 'В среду?', '09.02.07-251', 'среда')
    assert '403м' in answer and '2026–2027' in answer and 'Время звонков' in answer
    assert reviewed_timetable_answer(doc, '', '09.02.07-252', 'среда') is None
    assert 'проверенных строк нет' in reviewed_timetable_answer(doc, 'В 2025 году?', '09.02.07-251', 'среда')
    assert 'проверенных строк нет' in reviewed_timetable_answer(doc, 'Во втором семестре?', '09.02.07-251', 'среда')
    doc.content_hash = 'changed'
    assert reviewed_timetable_answer(doc, '', '09.02.07-251', 'среда') is None


def test_unverified_cells_are_never_used_and_partial_days_are_labelled():
    doc = document(); doc.meta['reviewed_timetable']['text_verified'] = False
    assert reviewed_timetable_answer(doc, '', '09.02.07-251', 'среда') is None
    doc.meta['reviewed_timetable']['text_verified'] = True
    doc.meta['reviewed_timetable']['excluded'] = [{'group': '09.02.07-251', 'day': 'среда', 'lesson': 2}]
    answer = reviewed_timetable_answer(doc, '', '09.02.07-251', 'среда')
    assert 'неполный список' in answer and 'числитель/знаменатель не определяю' in answer


def test_a_pdf_without_a_native_grid_cannot_produce_lessons():
    import pymupdf
    pdf = pymupdf.open(); page = pdf.new_page()
    page.insert_text((50, 50), '09.02.07-251 1 403')
    assert extract_page(page) == {'records': [], 'excluded': []}
    pdf.close()


def test_local_official_grid_matches_review_and_withholds_half_week_cells():
    # The official source is local-only; a clean checkout can run the other tests.
    import hashlib, json
    from pathlib import Path
    import pytest, pymupdf
    source = Path('data/raw/ncsa-cache/e322dff2e257dfbcc2bb.pdf')
    if not source.exists():
        pytest.skip('Official PDF is deliberately not stored in Git')
    review = json.loads(Path('docs/source-reviews/timetable-it-2026-page2.json').read_text(encoding='utf-8'))
    assert hashlib.sha256(source.read_bytes()).hexdigest() == review['source_sha256']
    with pymupdf.open(source) as pdf:
        result = extract_page(pdf[1])
    canonical = json.dumps(result['records'], ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    assert hashlib.sha256(canonical.encode()).hexdigest() == review['records_sha256']
    assert not any(r['day'] == 'четверг' and r['lesson'] == 3 for r in result['records'])
    monday = [r for r in result['records'] if r['group'] == '09.02.07-251' and r['day'] == 'понедельник']
    assert [(r['subject'], r['room']) for r in monday] == [('Ин.яз', '410 415'), ('ААС', '102')]
