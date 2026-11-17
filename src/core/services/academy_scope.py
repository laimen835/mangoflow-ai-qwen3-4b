"""Organisational identity is resolved before college shortcuts and retrieval.

Names/aliases describe units, never their changing personnel. Personnel are read
from the fetched official unit page, not hardcoded here.
"""
import re
from dataclasses import dataclass
from urllib.parse import urlsplit

from sqlalchemy import or_, and_
from src.core.db.models import Document


@dataclass(frozen=True)
class Unit:
    key: str
    name: str
    aliases: tuple[str, ...]
    paths: tuple[str, ...]

    @property
    def url(self):
        return 'https://ncsa.ru' + self.paths[0]


UNITS = (
    Unit('137', 'Институт цифровых технологий', ('цифров', 'пмиит', 'ицт', 'прикладной математики'), ('/institutes/137/', '/institutes/pmiit/')),
    Unit('138', 'Юридический институт', ('юридическ', 'юринститут'), ('/institutes/138/', '/institutes/ui/')),
    Unit('145', 'Аграрный институт', ('аграрн',), ('/institutes/145/', '/institutes/ai/')),
    Unit('146', 'Инженерный институт', ('инженерн',), ('/institutes/146/', '/institutes/ii/')),
    Unit('147', 'Институт открытого образования', ('открытого образован', 'иоо'), ('/institutes/147/', '/institutes/ioo/')),
    Unit('spk', 'Среднепрофессиональный колледж', ('колледж', 'спк', 'среднепрофессиональн', 'спо'), ('/institutes/148/', '/institutes/spk/')),
    Unit('149', 'Институт экономики и управления', ('экономики и управлен', 'иэиу'), ('/institutes/149/', '/institutes/ieiu/')),
    Unit('150', 'Медицинский институт', ('медицинск', 'мединститут'), ('/institutes/150/', '/institutes/mi/')),
    Unit('151', 'Институт дизайна и лингвистики', ('дизайн', 'лингвистик', 'идил'), ('/institutes/151/', '/institutes/fdii/')),
    Unit('358', 'Военный учебный центр', ('военн', 'вуц'), ('/institutes/358/', '/institutes/vuc/')),
)
ACADEMY = Unit('academy', 'Северо-Кавказская государственная академия', (), ('/',))


def normalized(text):
    return re.sub(r'\s+', ' ', text.casefold().replace('ё', 'е')).strip()


def explicit_units(text):
    text = normalized(text)
    acronyms = {'спк','спо','ицт','пмиит','иоо','иэиу','идил','вуц'}
    college = bool(re.search(r'колледж|\bспк\b|\bспо\b|среднепрофессиональн', text))
    institution = bool(re.search(r'институт|учебн\w* центр', text))
    role = bool(re.search(r'директор|руководит|начальник', text))
    result = []
    for unit in UNITS:
        matched = any(p in text for p in unit.paths)
        for alias in unit.aliases:
            pattern = r'(?<![а-яa-z])' + re.escape(alias) + (r'(?![а-яa-z])' if alias in acronyms else '')
            if not re.search(pattern, text):
                continue
            if unit.key == 'spk' or alias in acronyms or 'институт' in alias or institution or (role and not college):
                matched = True
        if matched:
            result.append(unit)
    return result


def resolve_units(text, history=()):
    units = explicit_units(text)
    if units:
        return units
    if re.search(r'\bректор|\bакадеми(?:я|и|ю|ей|е)\b|\bскга\b', normalized(text)):
        return [ACADEMY]
    # Only genuinely dependent turns inherit a unit. A new institute question
    # without its name must clarify instead of guessing from the previous turn.
    deictic=re.search(r'\b(?:его|ее|этого|этой|него|нее|там)\b',normalized(text))
    independent=re.search(r'^(?:а\s+)?(?:что такое|объясни|расскажи|напиши|посчитай)\b',normalized(text))
    if re.fullmatch(r'(?:а\s+)?(?:(?:расскажи|объясни)\s+)?(?:по)?подробнее\s*[?!.]*',normalized(text)):
        independent=None
    if (len(text) < 150 and not independent
            and (deictic or not re.search(r'институт|колледж|академ|центр', normalized(text)))
            and (deictic or re.search(r'^(а\b|и\b|его\b|ее\b|там\b|какая почта|какой телефон|как связаться|'
                          r'какие документы|какие направления|какие специальности|кто (?:его|ее)|что нужно|'
                          r'подробнее|расскажи подробнее|(?:почта|телефон|контакты|адрес)\s*[?!.]*$)', normalized(text)))):
        for turn in reversed(history):
            if turn.get('role') != 'user':
                continue
            from src.core.services.conversation_assistance import utility_kind
            if utility_kind(turn.get('content','')):break
            previous = explicit_units(turn.get('content', ''))
            if previous:
                return previous
            # A completed unrelated question ends the old unit context.
            break
    return []


def unit_for_url(url):
    path = urlsplit(url).path
    return next((u for u in UNITS if any(path.startswith(p) for p in u.paths)), None)


def unit_filter(keys):
    """Apply BEFORE top-k; missing legacy scope is inferred only from unit URLs."""
    units = [u for u in (*UNITS, ACADEMY) if u.key in keys]
    clauses = [Document.meta['unit_key'].astext.in_(list(keys))]
    if 'spk' in keys:
        clauses.append(and_(Document.meta['unit_key'].astext.is_(None),
                            or_(Document.source_url.is_(None),
                                ~Document.source_url.like('https://ncsa.ru/institutes/%'))))
    for unit in units:
        if unit.key != 'academy':
            clauses.extend(Document.source_url.startswith('https://ncsa.ru' + p) for p in unit.paths)
    return or_(*clauses)


def scope_metadata(url, html=None):
    unit = unit_for_url(url)
    result = {'unit_key': unit.key if unit else 'academy',
              'unit_name': unit.name if unit else ACADEMY.name,
              'scope_version': 'academy-v1'}
    is_landing = unit and (url == unit.url or (unit.key == 'spk' and
        url == 'https://ncsa.ru/institutes/spk/inst.php?ELEMENT_ID=148'))
    if is_landing and html:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, 'html.parser')
        root = soup.select_one('#ncsa-main') or soup.select_one('main')
        name = root.select_one('.uni-director__name') if root else None
        if name:
            # This compact card supplies the identity/contacts only. Other roles
            # (deputies, rector) require their own evidence via normal retrieval.
            card = name.find_parent(class_='uni-director__card') or name.parent
            result['unit_director'] = name.get_text(' ', strip=True)
            result['unit_card_quote'] = card.get_text(' ', strip=True)
            result['unit_card_phone'] = [a.get_text(' ', strip=True) for a in card.select('a[href^="tel:"]')]
            result['unit_card_email'] = [a.get_text(' ', strip=True) for a in card.select('a[href^="mailto:"]')]
    return result


def linked_department_scope(url, references):
    """Infer a neutral department URL only from explicit links on unit roots.

    Navigation association does not establish applicability of regulations.
    Shared departments remain academy-level instead of being assigned arbitrarily.
    """
    result=scope_metadata(url)
    parts=urlsplit(url)
    if (parts.scheme!='https' or parts.hostname!='ncsa.ru' or unit_for_url(url)
            or not parts.path.startswith('/about_the_university/structure/chairs/')):
        return result
    owners={}
    evidence=[]
    for ref in references:
        parent=ref.get('parent','');parent_parts=urlsplit(parent)
        owner=unit_for_url(parent)
        if (parent_parts.scheme!='https' or parent_parts.hostname!='ncsa.ru' or not owner
                or parent!=owner.url or ref.get('url')!=url):
            continue
        owners[owner.key]=owner
        evidence.append({'parent':parent,'linked_url':url,'link_text':ref.get('title','')})
    result.update(scope_evidence=evidence,scope_version='academy-parent-chairs-v1')
    if len(owners)==1:
        owner=next(iter(owners.values()))
        result.update(unit_key=owner.key,unit_name=owner.name)
    elif len(owners)>1:
        result['scope_ambiguous']=sorted(owners)
    return result


def requested_department_matches(query, department_name):
    """Match an explicitly named department from verified navigation metadata.

    This is a ranking hint, not an eligibility gate or proof of a fact.
    """
    if not department_name or not re.search(r'кафедр',normalized(query)):
        return False
    from src.core.rag.evidence_search import word_terms
    name_terms=set(word_terms(department_name))-{'кафедра'}
    return bool(name_terms) and name_terms.issubset(set(word_terms(query)))
