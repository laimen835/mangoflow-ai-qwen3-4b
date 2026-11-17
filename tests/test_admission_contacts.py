from types import SimpleNamespace
from src.core.services.admission_contacts import contact_request, read_contacts, format_contacts


def test_contacts_are_bound_to_labels_and_survive_chunk_overlap():
    text = 'Приемная комиссия. Наш адрес: г. Пример, корпус 2, кабинет 17 Телефоны: +7(800)-000-00-01, 8-900-000-00-02 Электронная почта: Электронная почта: admission@example.ru Материалы: документы'
    chunks = [SimpleNamespace(id=1, content=text[:120]), SimpleNamespace(id=2, content=text[85:])]
    facts = read_contacts(chunks)
    assert facts['address'] == 'г. Пример, корпус 2, кабинет 17'
    assert facts['email'] == 'admission@example.ru' and facts['chunk_id'] == 2
    assert '8-900-000-00-02' in format_contacts(facts, 'Телефон приёмной комиссии?')
    assert '17' in format_contacts(facts, 'В какой кабинет обращаться абитуриенту?')


def test_unlabelled_or_incomplete_contacts_do_not_produce_a_fact():
    assert read_contacts([SimpleNamespace(id=1, content='Наш адрес: неизвестен. +7(800)-000-00-01')]) is None
    assert contact_request('Какой телефон приёмной комиссии колледжа?')
    assert not contact_request('Какой личный телефон преподавателя?')
    assert not contact_request('Какие документы послать по почте в приёмную комиссию?')
    assert contact_request('В какую комнату идти сдавать документы абитуриенту?')
    assert not contact_request('Какие документы нужны и в какую комнату идти абитуриенту?')
