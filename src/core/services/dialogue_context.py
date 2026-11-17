"""Resolve short unit replies from user-authored context, never assistant facts."""
import re

from src.core.services.academy_scope import explicit_units,normalized


def bare_unit_reply(message):
    text=normalized(message).strip(' ?!.,—-')
    text=re.sub(r'^(?:а|и|нет|это)\s*[,—:]?\s+','',text)
    if (not text or len(text)>120 or len(text.split())>9
            or not re.fullmatch(r'[а-яa-z0-9\s-]+',text)
            or re.search(r'\b(?:кто|что|как|где|какие|какой|почему|сколько|расскажи|покажи|объясни|нужно|можно)\b',text)):
        return None
    units=explicit_units('институт '+text)
    if len(units)!=1:return None
    from src.core.rag.evidence_search import word_terms
    words=set(word_terms(text))
    allowed=set(word_terms(units[0].name+' '+' '.join(units[0].aliases)))
    return units[0] if words and words.issubset(allowed) else None


def replace_unit(question,old,new):
    """Replace only recognized unit tokens, preserving years, groups and names."""
    from src.core.rag.evidence_search import normal_word
    tokens=list(re.finditer(r'[а-яёa-z0-9]+',question,re.I))
    lemmas=[normal_word(t.group()) for t in tokens]
    patterns=[]
    for phrase in (old.name,*old.aliases):
        words=[normal_word(t) for t in re.findall(r'[а-яёa-z0-9]+',phrase,re.I)]
        if words:patterns.append(words)
    full=patterns[0]
    short=[w for w in full if w not in {'институт','колледж','центр'}]
    if short:patterns.append(short)
    patterns.sort(key=len,reverse=True)
    spans=[];index=0
    while index<len(tokens):
        match=next((p for p in patterns if lemmas[index:index+len(p)]==p),None)
        if match:
            spans.append((tokens[index].start(),tokens[index+len(match)-1].end()))
            index+=len(match)
        else:index+=1
    for start,end in reversed(spans):question=question[:start]+new.name+question[end:]
    return question if spans else None


def resolve_unit_reply(message,pending,prior):
    unit=bare_unit_reply(message)
    if unit is None:return message
    if isinstance(pending,dict) and pending.get('kind')=='unit' and isinstance(pending.get('question'),str):
        return pending['question']+'\nУточнённое подразделение: '+unit.name
    # A bare unit after an answered question can mean "and for this institute?".
    # Stop at the latest user turn: never resurrect a topic across a new question.
    previous=next((m['content'] for m in reversed(prior) if m.get('role')=='user'),None)
    if previous and re.search(r'директор|начальник|руководит|почт|телефон|адрес|контакт|поступ|специальност|направлен|расписан|стипенд|общежит',previous,re.I):
        old=explicit_units(previous)
        if not old and re.search(r'институт|колледж|учебн\w* центр',previous,re.I):
            # Compatibility with a clarification asked before pending-state
            # storage existed. Only the last user question supplies meaning.
            return previous+'\nУточнённое подразделение: '+unit.name
        if len(old)==1:
            return replace_unit(previous,old[0],unit) or message
    return message
