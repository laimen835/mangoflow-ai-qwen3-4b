from sqlalchemy.dialects import postgresql
from src.core.services.academy_scope import explicit_units, resolve_units, scope_metadata, unit_filter
from src.core.rag.ncsa_source import allowed_url, extract_page


def test_different_institutes_and_multiple_names():
    assert [u.key for u in explicit_units('Кто директор института цифровых технологий?')] == ['137']
    assert [u.key for u in explicit_units('Телефон юридического института')] == ['138']
    assert [u.key for u in explicit_units('Аграрный институт и колледж')] == ['145', 'spk']
    assert [u.key for u in explicit_units('Кто директор цифровых технологий?')] == ['137']


def test_subject_words_do_not_invent_institutes():
    assert [u.key for u in explicit_units('Нужна ли медицинская справка для поступления в колледж?')] == ['spk']
    assert [u.key for u in explicit_units('Нужен ли военный билет в колледже?')] == ['spk']
    assert [u.key for u in explicit_units('Есть ли специальность дизайн в СПК?')] == ['spk']
    assert explicit_units('Каким способом поступить?') == []
    assert resolve_units('Какие бывают академические стипендии?') == []


def test_followup_and_change_of_topic():
    turns = [{'role': 'user', 'content': 'Кто директор ИЦТ?'},
             {'role': 'assistant', 'content': 'Ошибочный ответ про колледж'}]
    assert [u.key for u in resolve_units('А какая у неё почта?', turns)] == ['137']
    assert [u.key for u in resolve_units('Кто директор колледжа?', turns)] == ['spk']
    assert resolve_units('Кто директор института?', turns) == []
    assert resolve_units('Расскажи о поступлении', turns) == []


def test_official_site_boundary():
    assert allowed_url('https://ncsa.ru/institutes/137/')
    assert allowed_url('https://ncsa.ru/sveden/grants/')
    assert allowed_url('https://ncsa.ru/')
    assert not allowed_url('https://evil.example/institutes/137/')
    assert not allowed_url('https://ncsa.ru/bitrix/admin/')
    assert not allowed_url('https://ncsa.ru/personal/')


def test_unit_card_has_own_identity_and_blank_heading_fallback():
    html = '''<main><h1></h1><div class="uni-director__card">
    <span class="uni-director__name">Тестовый Руководитель Института</span>
    <a href="mailto:ict@ncsa.ru">ict@ncsa.ru</a>
    <a href="tel:123">123</a></div><p>О цифровых технологиях.</p></main>'''
    url = 'https://ncsa.ru/institutes/137/'
    meta = scope_metadata(url, html)
    assert meta['unit_key'] == '137'
    assert meta['unit_director'] == 'Тестовый Руководитель Института'
    assert meta['unit_card_email'] == ['ict@ncsa.ru']
    assert 'Тестовый Руководитель' in meta['unit_card_quote']
    assert extract_page(html, url)[0] == 'Институт цифровых технологий'
    assert 'unit_director' not in scope_metadata('https://ncsa.ru/institutes/138/other/', html)


def test_scope_applies_to_documents_before_top_k():
    clause = str(unit_filter(('137',)).compile(dialect=postgresql.dialect(), compile_kwargs={'literal_binds': True}))
    assert '137' in clause and '/institutes/pmiit/' in clause
    assert '/institutes/spk/' not in clause
