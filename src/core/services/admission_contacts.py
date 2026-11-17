"""Read labelled public admissions contacts from the indexed official page."""
import re
from src.core.services.specialties import reconstruct


def contact_request(question):
    text = question.casefold()
    room_request = bool(re.search(r'^\s*(?:в\s+како[йем]|в\s+какую|где)\b.{0,35}(?:кабинет|комнат)', text))
    return (bool(re.search(r'при[её]мн|абитуриент', text))
            and bool(re.search(r'телефон|позвон|почт|кабинет|комнат|адрес|связаться', text))
            and not bool(re.search(r'стоимост|личн|бывш|20\d{2}|директор', text))
            and (room_request or not re.search(r'документ', text)))


def read_contacts(chunks):
    text = reconstruct(chunks)
    match = re.search(r'Наш адрес:\s*(?P<address>.{5,220}?)\s*Телефоны?:\s*'
                      r'(?P<phone>[+\d()\s,;\-]{8,100})\s*'
                      r'(?:Электронная почта:\s*)+(?P<email>[\w.+-]+@[\w.-]+)', text, re.I)
    if not match:
        return None
    return {**match.groupdict(), 'address': re.sub(r'\s*Схема проезда\s*$', '', match['address']),
            'quote': match.group(),
            'chunk_id': next((c.id for c in chunks if match['email'] in c.content), None)}


def format_contacts(facts, question):
    text = question.casefold()
    if re.search(r'телефон|позвон', text):
        return f"Телефоны приёмной комиссии СПК: {facts['phone'].strip()} [1]."
    if re.search(r'почт', text):
        return f"Электронная почта приёмной комиссии СПК: {facts['email']} [1]."
    return f"Адрес приёмной комиссии СПК: {facts['address']} [1]."
