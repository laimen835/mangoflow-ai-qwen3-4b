from types import SimpleNamespace
from src.core.rag.answerability import asks_application_recipient, recipient_evidence_missing


def hit(text, restored=False):
    return SimpleNamespace(content=text, meta={'section_context_restored': restored})


def test_eligible_category_and_link_title_do_not_prove_application_recipient():
    question='Кому подают заявление на заселение по положению об общежитии?'
    assert asks_application_recipient(question)
    assert recipient_evidence_missing(question, [hit('Сведения о местах для иногородних поступающих (https://ncsa.ru/file.pdf)')])
    assert recipient_evidence_missing(question, [hit('Иногородним студентам предоставляется общежитие.')])
    assert not asks_application_recipient('Кому доступна материальная помощь?')


def test_explicit_direction_or_verified_blank_form_can_establish_recipient():
    question='На чьё имя подать заявление о материальной помощи?'
    assert not recipient_evidence_missing(question, [hit('Заявление подается в приёмную комиссию колледжа.')])
    assert not recipient_evidence_missing(question, [hit('Заявление на имя ректора подают лично.')])
    template='Образец заявления. Ректору Академии Петрову П. П. обучающегося ___ курса. Заявление. Прошу оказать поддержку.'
    assert not recipient_evidence_missing(question, [hit(template, restored=True)])
    assert recipient_evidence_missing(question, [hit(template)])
    assert recipient_evidence_missing(question, [hit('Заявление не подается директору колледжа.')])
