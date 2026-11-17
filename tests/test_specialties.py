from types import SimpleNamespace
from src.core.services.specialties import specialty_fields,specialty_request,format_specialty


def test_specialty_qualification_and_duration_are_source_fields():
    text='👷 Строительство (08.02.01) Квалификация: Техник Срок обучения: 3 г. 10 мес. Объекты деятельности: здания.'
    records=specialty_fields([SimpleNamespace(id=7,content=text)])
    r=records['08.02.01'];assert r['qualification']=='Техник' and (r['years'],r['months'])==(3,10)
    assert r['name']=='Строительство' and r['chunk_id']==7
    assert 'Техник' in format_specialty(r,'Какая квалификация по 08.02.01?')


def test_overlap_is_removed_before_reading_fields_and_group_question_is_not_intercepted():
    text='🖥 Тестовое направление (09.02.12) Квалификация: Специалист по системам Срок обучения: 2 г. 10 мес.'
    chunks=[SimpleNamespace(id=1,content=text[:75]),SimpleNamespace(id=2,content=text[40:])]
    assert specialty_fields(chunks)['09.02.12']['months']==10
    assert specialty_request('Какая первая пара у 09.02.12-251?') is None
    assert specialty_request('Сколько учиться на 09.02.12?')=='09.02.12'


def test_no_degree_is_invented_for_unknown_or_incomplete_card():
    chunks=[SimpleNamespace(id=1,content='Специальность (09.02.12), сведения уточняются.')]
    assert specialty_fields(chunks)=={}


def test_conflicting_duration_does_not_hide_an_agreed_qualification():
    first='💡 Электроснабжение (13.02.07) Квалификация: Техник Срок обучения: 3 г. 10 мес.'
    second='💡 Электроснабжение (13.02.07) Квалификация: Техник Срок обучения: 2 г. 10 мес.'
    r=specialty_fields([SimpleNamespace(id=1,content=first),SimpleNamespace(id=2,content=second)])['13.02.07']
    assert 'Техник' in format_specialty(r,'Какая квалификация по 13.02.07?')
    answer=format_specialty(r,'Какой срок обучения по 13.02.07?')
    assert 'разные сроки' in answer and 'Уточните' in answer


def test_duration_paraphrases_use_the_same_actual_field():
    r=specialty_fields([SimpleNamespace(id=1, content='🎨 Дизайн (54.02.01) Квалификация: Дизайнер Срок обучения: 3 г. 10 мес.')])['54.02.01']
    for question in ['Какова продолжительность обучения по дизайну 54.02.01?',
                     'Какая длительность обучения по 54.02.01?', 'Как долго учиться на 54.02.01?']:
        assert specialty_request(question)=='54.02.01'
        assert '3 г. 10 мес.' in format_specialty(r,question)
