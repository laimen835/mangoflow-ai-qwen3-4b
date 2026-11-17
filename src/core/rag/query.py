"""Cheap, auditable query expansion: history supplies topics, never facts."""
import re

_TOPIC = re.compile(r"поступ|при[её]мн|общежит|стипенд|специальност|преподав|директор|расписан|\d{2}\.\d{2}\.\d{2}", re.I)
_FOLLOWUP = re.compile(r"^(?:а\s|и\s|а?\s*(?:сколько стоит|какие документы|какие бумаги|что нужно|какие нужны|подробнее|расскажи подробнее))|\b(?:там|туда|него|неё|нее|этого|этой|это|этим|них|они)\b", re.I)


def contextual_query(question: str, history: list[dict]) -> str:
    """Resolve a short follow-up using at most three previous user turns.

    Explicit new topics stay independent. Assistant answers are excluded so
    hallucinated names or amounts cannot become retrieval facts.
    """
    previous_users=[m['content'] for m in history if m.get('role')=='user' and isinstance(m.get('content'),str)]
    if previous_users:
        from src.core.services.conversation_assistance import utility_kind
        if utility_kind(previous_users[-1]):return question
    if re.match(r'^(?:а\s+)?(?:что такое|напиши|посчитай)\b',question,re.I):
        return question
    if previous_users and len(question.split())<=8 and re.match(r'^(а|и)\b',question,re.I):
        identifier=re.search(r'\d{2}\.\d{2}\.\d{2}(?:-\d{3})?',question)
        year=re.search(r'\b20\d{2}\b',question)
        previous=previous_users[-1]
        identifier_only=identifier and re.fullmatch(r'(?:а|и)\s+(?:у\s+|для\s+|по\s+)?'+re.escape(identifier.group())+r'\s*[?!.]*',question.strip(),re.I)
        year_only=year and re.fullmatch(r'(?:а|и)\s+(?:в\s+|на\s+)?'+year.group()+r'(?:\s+году)?\s*[?!.]*',question.strip(),re.I)
        if identifier_only and re.search(r'\d{2}\.\d{2}\.\d{2}(?:-\d{3})?',previous):
            return re.sub(r'\d{2}\.\d{2}\.\d{2}(?:-\d{3})?',identifier.group(),previous)
        if year_only and re.search(r'\b20\d{2}\b',previous):
            return re.sub(r'\b20\d{2}\b',year.group(),previous)
    if len(question.split()) > 18 or _TOPIC.search(question) or not _FOLLOWUP.search(question):
        return question
    previous = previous_users[-3:]
    anchor = next((i for i in range(len(previous)-1, -1, -1) if _TOPIC.search(previous[i])), None)
    if anchor is None:
        return question
    context = " ".join(previous[anchor:])[:600]
    return f"{context}\nУточнение: {question}"


def expand_query(query: str) -> str:
    """Add institution-specific vocabulary without altering original entities."""
    rules = [
        (r"расшифр|аббревиатур", "полное наименование"),
        (r"бумаг|принести|подать заявлен", "документы"),
        (r"приезж|иногород|жить|жиль[её]|крыша над голов", "общежитие проживание"),
        (r"сколько стоит|платить", "стоимость обучение оплата"),
        (r"(?:после|окончи\w*|на базе)\s+(?:11\b|одиннадцат)", "среднее общее образование"),
        (r"(?:после|окончи\w*|на базе)\s+(?:9\b|девят)", "основное общее образование"),
        (r"денежн.*поддерж|пособи", "стипендия социальная поддержка"),
        (r"матан", "математический анализ"),
        (r"основан|создан.*колледж|истори.*колледж", "основание техникума история"),
    ]
    additions = [words for pattern, words in rules if re.search(pattern, query, re.I)]
    return query + ("\n" + " ".join(additions) if additions else "")


def focus_terms(query: str) -> list[str]:
    """Prefer the requested fact over generic words in a matching document."""
    # "Document" is a source format, not a topic. Requiring it loses clauses
    # that use the actual terms (charter, passport, application, etc.).
    subjects = ["общежити", "стоимост", "стипенди", "основан", "диплом",
                "выпуск", "библиотек", "спортивн", "компьютерн", "волонтер", "смагин"]
    text = query.casefold().replace('ё', 'е')
    return [term for term in subjects if term in text]
