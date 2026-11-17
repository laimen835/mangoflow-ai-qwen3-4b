from datetime import date
import pytest

from src.core.services.directory import extract_groups, find_teachers, normalize, week_status


@pytest.mark.parametrize("value", ["Соловьёва", "СОЛОВЬЕВА", "Соловьева "])
def test_normalize_yo_and_case(value):
    assert normalize(value) == "соловьева"


def test_fuzzy_surname_is_a_suggestion_not_an_identity_assertion():
    people = [{"name": "Черкашина Елена Леонидовна", "departments": ["Технологическое отделение"]}]
    assert find_teachers(people, "Черкашна") == (people, True)
    assert find_teachers(people, "Несуществующий") == ([], False)


@pytest.mark.parametrize("day,expected", [(date(2026,9,1),"numerator"), (date(2026,9,6),"numerator"),
    (date(2026,9,7),"denominator"), (date(2026,9,14),"numerator"), (date(2026,8,31),"numerator")])
def test_week_alternation_uses_mondays_and_verified_anchor(day, expected):
    assert week_status(day,date(2026,9,1),"numerator","https://ncsa.ru/calendar")["type"] == expected


def test_unverified_calendar_never_invents_week_type():
    assert week_status(date(2026,10,29),None,"numerator","")["type"] is None
    assert week_status(date(2026,10,29),date(2026,9,1),"numerator","")["type"] is None


def test_group_codes_are_distinct_and_deduplicated():
    assert extract_groups("09.02.07-251 09.02.07-251 40.02.04-262") == ["09.02.07-251","40.02.04-262"]
    assert extract_groups("09.02.07 – 251") == ["09.02.07-251"]


def test_search_keeps_speciality_and_group_codes_whole():
    from src.core.rag.bm25_search import query_terms
    assert "09.02.07" in query_terms("Какая специальность имеет код 09.02.07?")
    assert "09.02.07-251" in query_terms("Расписание группы 09.02.07-251")


def test_full_name_inflection_disambiguates_similar_surnames():
    from src.core.services.directory import directory_matches, named_directory_request
    male = {'name': 'Аджиев Алим Мухамедович', 'departments': ['ИТ']}
    female = {'name': 'Аджиева Кулистан Исмаиловна', 'departments': ['Право']}
    people = [male, female]
    question = 'В каком отделении работает преподаватель Аджиева Кулистан Исмаиловна?'
    assert directory_matches(people, question) == ([female], False)
    assert named_directory_request(people, 'Отделение Аджиевой Кулистан Исмаиловны?') == [female]


def test_search_normalizes_yo_consistently_but_preserves_codes():
    from src.core.rag.bm25_search import query_terms
    from src.core.rag.query import focus_terms
    assert query_terms('Волонтёрское движение 09.02.07-251') == query_terms('Волонтерское движение 09.02.07-251')
    assert focus_terms('волонтёрское движение') == ['волонтер']
