from src.core.rag.answer_review import validate_review
from src.core.rag.vector_search import SearchHit


def hits():
    return [SearchHit(1,1,'4.9. Поддержка молодых студенческих семей: в течение 12 месяцев с даты регистрации брака.',
        'Положение','https://ncsa.ru/policy.pdf',1,1,'test',meta={'section':'4.9'})]


def test_supported_claim_has_exact_quote_and_source_number():
    d={'claims':[{'text':'Срок обращения после регистрации брака — 12 месяцев','source':1,
                 'quote':'в течение 12 месяцев с даты регистрации брака.'}],'incomplete':False}
    r=validate_review(d,hits());assert r.status=='checked' and '[1]' in r.text


def test_invented_quote_or_source_cannot_be_published():
    for source,quote in [(2,'в течение 12 месяцев'),(1,'в течение 24 месяцев')]:
        r=validate_review({'claims':[{'text':'Срок 24 месяца','source':source,'quote':quote}]},hits())
        assert r.text is None and r.status=='no_verified_claims'


def test_extra_number_and_global_absence_claim_are_withheld():
    for text in ['Выплата составляет 5000 рублей','Документ не уточняет, что помощь предназначена молодым семьям']:
        r=validate_review({'claims':[{'text':text,'source':1,'quote':'в течение 12 месяцев с даты регистрации брака.'}]},hits())
        assert r.text is None


def test_discarded_claims_mark_answer_partial_instead_of_silent_success():
    r=validate_review({'claims':[{'text':'Срок обращения — 12 месяцев','source':1,'quote':'в течение 12 месяцев'},
        {'text':'Гарантировано 5000 рублей','source':1,'quote':'в течение 12 месяцев'}]},hits())
    assert r.status=='partial' and 'Подтверждена только часть' in r.text and '5000' not in r.text


def test_copy_typo_can_only_retain_a_long_exact_core_from_actual_source():
    from src.core.rag.answer_review import exact_quote_part,norm
    source='единоразовая финансовая поддержка при создании семьи в течение 12 месяцев с даты регистрации брака'
    quote='одиноразовая финансовая поддержка при создании семьи в течение 12 месяцев с даты регистрации брака'
    retained,adjusted=exact_quote_part(quote,source)
    assert adjusted and norm(retained) in norm(source) and '12 месяцев' in retained
    assert 'одиноразовая' not in retained
    assert exact_quote_part('в течение 24 месяцев',source)[0] is None


def test_unsolicited_absence_claim_does_not_override_present_family_condition():
    from src.core.rag.answer_review import remove_unproven_absence
    answer='Срок — 12 месяцев [1]. Документ не уточняет, что помощь касается молодых семей.\n\nИсточник: [1]'
    text,removed=remove_unproven_absence(answer,hits())
    assert removed==1 and '12 месяцев [1]' in text and 'не уточняет' not in text


def test_literal_negative_rule_is_preserved_and_empty_result_is_honest():
    from src.core.rag.answer_review import remove_unproven_absence
    rows=hits();rows[0].content='Документ не предусматривает автоматическую выплату.'
    text,n=remove_unproven_absence('Документ не предусматривает автоматическую выплату.',rows)
    assert n==0 and 'не предусматривает' in text
    text,n=remove_unproven_absence('Документ не содержит никаких условий.',hits())
    assert n==1 and 'Это не означает' in text
