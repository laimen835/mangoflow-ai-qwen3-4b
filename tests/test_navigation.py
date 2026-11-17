from src.core.rag.navigation import navigation_answer, exact_fact_answer
from src.core.rag.vector_search import SearchHit


def hit(text):
    return SearchHit(1,1,text,'Поступающим','https://ncsa.ru/institutes/spk/',1,1.0,'bm25')


def test_hostel_does_not_point_to_admission_quota():
    sources=[hit('Количество мест по специальностям https://ncsa.ru/quota.docx\n'
                 'Общежитие https://ncsa.ru/institutes/spk/o-nalichii-obshchezhitiya.php')]
    answer=navigation_answer('Где посмотреть условия проживания в общежитии?',sources)
    assert 'o-nalichii-obshchezhitiya.php' in answer
    assert 'quota.docx' not in answer
    assert '[1]' in answer


def test_documents_year_is_explicit_and_respected():
    sources=[hit('https://ncsa.ru/institutes/spk/Perechen_dokum_2026.pdf')]
    assert '2026 год' in navigation_answer('Где найти перечень документов для поступления?',sources)
    assert navigation_answer('Где перечень документов для поступления в 2027 году?',sources) is None


def test_no_matching_official_link_means_no_navigation_answer():
    assert navigation_answer('Где документы для поступления?', [hit('https://evil.test/Perechen_dokum_2026.pdf')]) is None
    assert navigation_answer('Кто директор?', [hit('https://ncsa.ru/')]) is None


def test_diploma_issuer_is_copied_from_source_not_hardcoded():
    assert 'Диплом ФГБОУ ВО «Пример» [1]' in exact_fact_answer('Какой диплом выдаётся?', [hit('Диплом\nФГБОУ ВО «Пример»')])
    assert exact_fact_answer('Когда диплом?', [hit('Диплом ФГБОУ ВО «Пример»')]) is None
    assert exact_fact_answer('Какой диплом?', [hit('Без указания организации')]) is None
