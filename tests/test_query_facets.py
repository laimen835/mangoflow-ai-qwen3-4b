from src.core.rag.query import expand_query, focus_terms


def test_paid_form_does_not_automatically_turn_a_support_question_into_a_price_search():
    question='Как финансируется помощь студентам платной формы?'
    assert expand_query(question)==question
    assert 'стоимость' in expand_query('Сколько стоит обучение?')


def test_source_format_is_not_a_required_topic_but_identifiers_and_years_stay_intact():
    question='Какие документы определяют деятельность СПК в 2026 году?'
    assert focus_terms(question)==[]
    query='Можно поступить после одиннадцатого класса на 09.02.07 в 2026 году?'
    expanded=expand_query(query)
    assert expanded.startswith(query) and 'среднее общее образование' in expanded
    assert 'основное общее образование' in expand_query('Поступление после 9 класса')
