from __future__ import annotations

import difflib
import re
from functools import lru_cache
from datetime import date, timedelta

from bs4 import BeautifulSoup

DEPARTMENTS = {"tab-tech": "Технологическое отделение", "tab-it": "Информационные технологии",
               "tab-law": "Управление и право", "tab-design": "Проектирование и приборостроение"}


def normalize(value: str) -> str:
    return re.sub(r"[^а-яa-z0-9]+", " ", value.casefold().replace("ё", "е")).strip()


def extract_teachers(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    faculty = soup.select_one('.uni-tabs__pane[data-pane="faculty"]')
    if faculty is None:
        return []
    people = {}
    for pane_id, department in DEPARTMENTS.items():
        pane = faculty.find(id=pane_id)
        if pane is None:
            continue
        leader = pane.select_one("h5.card-title")
        names = [n.get_text(" ", strip=True) for n in pane.select(".col-md-4.col-sm-6.mb-2")]
        if leader:
            names.append(leader.get_text(" ", strip=True))
        for name in names:
            if len(name.split()) < 3 or not re.fullmatch(r"[А-Яа-яЁё \-]+", name):
                continue
            key = normalize(name)
            person = people.setdefault(key, {"name": name, "departments": [], "role": "Преподаватель"})
            if department not in person["departments"]:
                person["departments"].append(department)
            if leader and normalize(leader.get_text(" ", strip=True)) == key:
                person["role"] = "Заведующий отделением"
    return sorted(people.values(), key=lambda p: p["name"])


def find_teachers(people: list[dict], query: str) -> tuple[list[dict], bool]:
    query = normalize(query)
    if not query:
        return people, False
    tokens = query.split()
    exact = [p for p in people if all(t in normalize(p["name"] + " " + " ".join(p["departments"])) for t in tokens)]
    if exact:
        return exact, False
    if len(tokens) == 1 and len(query) >= 4:
        matches = [p for p in people if difflib.SequenceMatcher(None, query,
                   normalize(p["name"]).split()[0]).ratio() >= 0.78]
        return matches, bool(matches)
    return [], False


def named_directory_request(people: list[dict], query: str) -> list[dict]:
    """Recognize a surname in a directory question without requiring 'teacher'."""
    text = normalize(query)
    if not re.search(r'отделени|справочник|работает|преподавател|препод', text):
        return []
    # Contact, biographical and private-data questions require other evidence.
    if re.search(r'телефон|почт|зарплат|жив[её]т|возраст|образован|диплом', text):
        return []
    matches, approximate = directory_matches(people, query)
    return [] if approximate else matches


@lru_cache(maxsize=1)
def _name_morph():
    from pymorphy3 import MorphAnalyzer
    return MorphAnalyzer()


@lru_cache(maxsize=4096)
def _name_forms(token):
    # Inflect an actual directory name; never manufacture an identity from
    # the question. Cache keeps repeated requests inexpensive.
    forms = {token}
    for parse in _name_morph().parse(token):
        if any(tag in parse.tag for tag in ('Name', 'Surn', 'Patr')):
            forms.update(normalize(form.word) for form in parse.lexeme)
    return forms


def directory_matches(people: list[dict], query: str) -> tuple[list[dict], bool]:
    tokens = set(normalize(query).split())
    scored = []
    for person in people:
        parts = normalize(person['name']).split()
        if not parts or not (_name_forms(parts[0]) & tokens):
            continue
        score = sum(bool(_name_forms(part) & tokens) for part in parts)
        scored.append((score, person))
    if scored:
        best = max(score for score, _ in scored)
        return [person for score, person in scored if score == best], False
    # Approximate surname matches remain suggestions; don't stop at the
    # first ordinary query word with a coincidental match.
    guesses = []
    for person in people:
        surname = normalize(person['name']).split()[0]
        score = max((difflib.SequenceMatcher(None, token, surname).ratio()
                     for token in tokens if len(token) >= 4), default=0)
        if score >= .78:
            guesses.append((score, person))
    if not guesses:
        return [], False
    best = max(score for score, _ in guesses)
    return [person for score, person in guesses if score == best], True


def department_head_request(people, question):
    text = normalize(question)
    if not re.search(r'кто.*(?:возглав|завед|руководит).*отделени', text):
        return []
    if re.search(r'почт|телефон|диагноз|парол|бывш|раньше', text):
        return []
    lemmas = {_name_morph().parse(t)[0].normal_form for t in text.split()}
    matched = []
    for person in people:
        if person.get('role') != 'Заведующий отделением':
            continue
        for department in person.get('departments', []):
            terms = {_name_morph().parse(t)[0].normal_form for t in normalize(department).split()
                     if t not in {'и', 'отделение'}}
            if terms and terms <= lemmas:
                matched.append(person)
                break
    return matched


def extract_groups(text: str) -> list[str]:
    return sorted({re.sub(r"\s+", "", value).replace("–", "-") for value in
                   re.findall(r"\b\d{2}\.\d{2}\.\d{2}\s*[-–]\s*\d{3}\b", text)})


def week_status(today: date, anchor: date | None, anchor_type: str, source: str) -> dict:
    monday = today - timedelta(days=today.weekday())
    result = {"date": today.isoformat(), "week_start": monday.isoformat(),
              "week_end": (monday + timedelta(days=6)).isoformat(), "type": None,
              "label": "Тип недели не подтверждён", "source_url": source or None,
              "explanation": "В приложении пока нет проверенной календарной привязки числителя и знаменателя."}
    if anchor and source and anchor_type in {"numerator", "denominator"}:
        anchor_monday = anchor - timedelta(days=anchor.weekday())
        offset = (monday - anchor_monday).days // 7
        parity = anchor_type if offset % 2 == 0 else ("denominator" if anchor_type == "numerator" else "numerator")
        result.update(type=parity, label="Числитель" if parity == "numerator" else "Знаменатель",
                      explanation="Рассчитано по подтверждённой опорной неделе; учебные исключения проверяйте в календаре.")
    return result
