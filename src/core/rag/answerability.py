"""Check explicit evidence for a requested application recipient.

The category eligible for housing/support is not the addressee of an
application. A link with that category in its title proves neither relation.
"""
import re


def asks_application_recipient(question):
    text = question.casefold().replace('ё', 'е')
    return bool('заявлен' in text and re.search(r'\bкому\b|на чье имя|на имя кого', text))


def recipient_evidence_missing(question, hits):
    if not asks_application_recipient(question):
        return False
    role = r'(?:ректор|директор|заведующ|комисси|декан|учебн\w* част|канцеляр|соцзащит|мфц|академи|колледж|фэу|убу)'
    relation = re.compile(r'заявлен\w*[^.!?;]{0,120}?(?:на имя|пода\w*|направля\w*|переда\w*)[^.!?;]{0,90}?' + role)
    for hit in hits:
        text = re.sub(r'\s+', ' ', hit.content).casefold().replace('ё', 'е')
        for match in relation.finditer(text):
            if not re.search(r'\bне\s+(?:пода|направля|переда)', match[0]):
                return False
        # A verified blank form also specifies a recipient before its title.
        if (hit.meta.get('section_context_restored') and 'образец заявления' in text
                and re.search(r'(?:ректору|директору|заведующему).{0,400}\bзаявление\b', text)):
            return False
    return True
