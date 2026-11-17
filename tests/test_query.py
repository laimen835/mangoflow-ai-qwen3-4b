import pytest
from src.core.rag.query import contextual_query, expand_query


@pytest.mark.parametrize('question', ['А какие документы нужны?', 'А сколько стоит?', 'Что нужно?', 'Как туда подать заявление?'])
def test_followup_keeps_original_topic(question):
    h = [{'role':'user','content':'Поступление на 09.02.07 после девятого класса'},
         {'role':'assistant','content':'Неподтверждённая сумма 999999'}]
    query = contextual_query(question,h)
    assert '09.02.07' in query and question in query and '999999' not in query


@pytest.mark.parametrize('question', ['Кто директор СПК?', 'Есть ли общежитие?', 'Какая погода на Марсе?', 'Группа 09.02.07-251'])
def test_new_topic_is_independent(question):
    assert contextual_query(question,[{'role':'user','content':'Какие документы для поступления?'}]) == question


def test_chain_and_entity_preservation():
    history = [{'role':'user','content':'Поступление на 09.02.07'},
               {'role':'user','content':'А какие документы нужны?'}]
    assert '09.02.07' in contextual_query('А куда их подавать?',history)
    assert contextual_query('А куда?',[]) == 'А куда?'
    original = 'Какие бумаги принести на 09.02.07-251?'
    assert expand_query(original).startswith(original)
    assert 'документы' in expand_query(original)
    assert 'общежитие' in expand_query('Где жить приезжим учащимся?')


def test_assistant_cannot_invent_a_topic():
    assert contextual_query('А сколько стоит?', [{'role':'assistant','content':'Обучение на 09.02.07'}]) == 'А сколько стоит?'
