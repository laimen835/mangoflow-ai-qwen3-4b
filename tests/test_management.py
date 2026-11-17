from types import SimpleNamespace
from src.core.services.management import management_fields, requested_management, format_management
from src.core.services.directory import named_directory_request


def test_management_uses_named_contact_rows_instead_of_college_header_email():
    text = ('Директор колледжа Общая почта: public@example.ru. Контакты Руководство '
            'Директор Петров Пётр Петрович (https://ncsa.ru/about_the_university/management/100/) '
            'Городской: Телефон: +7 (800) 000-00-01 Внутренний: - Email: Электронная почта: director@example.ru '
            'Заместитель директора по учебной работе Сидорова Мария Ивановна '
            '(https://ncsa.ru/about_the_university/management/101/) Email: deputy@example.ru')
    records = management_fields([SimpleNamespace(id=1, content=text)])
    assert len(records) == 2
    assert records[0]['email'] == 'director@example.ru'
    assert records[0]['phone'] == '+7 (800) 000-00-01'
    assert 'director@example.ru' in format_management(records[0], 'Почта директора?')
    assert requested_management('Кто заместитель директора по учебной работе?') == records[1]['role']
    assert requested_management('Кто был первым директором?') is None


def test_named_directory_question_without_teacher_keyword_and_privacy_guard():
    people = [{'name': 'Петров Пётр Петрович', 'departments': ['Отделение А'], 'role': 'Преподаватель'}]
    assert named_directory_request(people, 'Есть ли в справочнике Петров и какое у него отделение?') == people
    assert named_directory_request(people, 'Какой личный телефон Петрова из отделения?') == []


def test_director_credentials_are_read_from_named_source_header():
    from src.core.services.management import director_credentials, director_credentials_request
    chunks = [SimpleNamespace(content='Директор колледжа: Петров Пётр Петрович, кандидат технических наук, доцент. Образование: институт.')]
    assert director_credentials_request('Какое учёное звание или степень у директора СПК?')
    value, quote = director_credentials(chunks, 'Петров Пётр Петрович')
    assert value == 'кандидат технических наук, доцент' and value in quote
    assert director_credentials(chunks, 'Другой Человек Иванович') is None


def test_department_head_uses_directory_role_and_inflected_department_name():
    from src.core.services.directory import department_head_request
    head = {'name': 'Петров Пётр Петрович', 'role': 'Заведующий отделением', 'departments': ['Управление и право']}
    teacher = {'name': 'Сидоров Иван Иванович', 'role': 'Преподаватель', 'departments': ['Управление и право']}
    assert department_head_request([head, teacher], 'Кто возглавляет отделение управления и права?') == [head]
    assert department_head_request([head, teacher], 'Кто возглавляет технологическое отделение?') == []
