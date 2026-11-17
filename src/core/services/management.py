"""Explicit personnel/contact rows of the college's public directory."""
import re
from src.core.services.specialties import reconstruct

ROLE = r'(?:Заместитель директора по (?:учебной|воспитательной) работе|Директор)'
NAME_PART = r'[А-ЯЁ][а-яё]+(?:-[А-ЯЁа-яё]+)*'
PERSON = rf'{NAME_PART}\s+{NAME_PART}\s+{NAME_PART}'
PATTERN = re.compile(rf'(?P<role>{ROLE})\s+(?P<name>{PERSON})\s*'
                     r'\(https://ncsa\.ru/about_the_university/management/\d+/\)')


def management_fields(chunks):
    text = reconstruct(chunks); matches = list(PATTERN.finditer(text)); records = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        tail = text[match.end():min(end, match.end() + 400)]
        # A dash is an absent public value, never an invitation to guess it.
        email = re.search(r'Email:\s*(?:Электронная почта:\s*)?([\w.+-]+@[\w.-]+)', tail)
        phone = re.search(r'Городской:\s*Телефон:\s*(\+?\d[\d()\s-]{7,35})', tail)
        record = {**match.groupdict(), 'email': email[1] if email else None,
                  'phone': phone[1].strip() if phone else None,
                  'quote': text[match.start():match.end()] + ' ' + tail,
                  'chunk_id': next((c.id for c in chunks if re.sub(r'\s+', ' ', c.content).find(match['name']) >= 0), None)}
        records.append(record)
    return records


def requested_management(question):
    text = question.casefold()
    if re.search(r'бывш|первый|раньше|истори|20\d{2}', text):
        return None
    if 'зам' in text and 'директор' in text:
        if 'учебн' in text: return 'Заместитель директора по учебной работе'
        if 'воспитательн' in text: return 'Заместитель директора по воспитательной работе'
    if 'директор' in text and 'зам' not in text and re.search(r'почт|email|телефон', text):
        return 'Директор'
    return None


def format_management(record, question):
    text = question.casefold()
    value = record['name']
    if re.search(r'почт|email', text):
        if not record['email']: return None
        value += '; электронная почта: ' + record['email']
    elif 'телефон' in text:
        if not record['phone']: return None
        value += '; телефон: ' + record['phone']
    return f"В контактной таблице руководства указано [1]: {record['role']} — {value}."


def director_credentials_request(question):
    text = question.casefold().replace('ё', 'е')
    return ('директор' in text and bool(re.search(r'учен\w* (?:степен|зван)|квалификац', text))
            and not re.search(r'зам|бывш|перв|\b(?:19|20)\d{2}\b', text))


def director_credentials(chunks, name):
    if not name:
        return None
    text = reconstruct(chunks)
    match = re.search(r'Директор колледжа:\s*' + re.escape(name)
                      + r',\s*(?P<value>[^:]{5,200}?)\.?\s+Образование:', text)
    if not match or not re.search(r'доктор|кандидат|доцент|профессор', match['value'], re.I):
        return None
    quote = match[0].removesuffix('Образование:').strip()
    return match['value'].rstrip('.'), quote
