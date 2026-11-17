"""Conservative unit-name interpretation; original messages are never replaced."""
from difflib import SequenceMatcher
import re

from src.core.services.academy_scope import UNITS,explicit_units,normalized
from src.core.rag.evidence_search import normal_word


def interpret_unit_spelling(question):
    if explicit_units(question):
        return question
    text=normalized(question)
    if not re.search(r'инст|колл?е[дт]ж|универ|директ|руковод',text):
        return question
    words=[normal_word(w) for w in re.findall(r'[а-яё]+',text)]
    scores=[]
    for unit in UNITS:
        best=0
        # Full names avoid resolving an ordinary word such as 'дизайн' to an
        # institute without its organizational context.
        candidates=[' '.join(normal_word(w) for w in re.findall(r'[а-яё]+',normalized(unit.name)))]
        if unit.key=='spk':
            candidates.append('колледж')
        for name in candidates:
            size=len(name.split())
            for i in range(len(words)-size+1):
                phrase=' '.join(words[i:i+size])
                best=max(best,SequenceMatcher(None,phrase,name).ratio())
        scores.append((best,unit))
    scores.sort(key=lambda pair:pair[0],reverse=True)
    if scores and scores[0][0]>=.86 and scores[0][0]-scores[1][0]>=.12:
        return question+'\nПредполагаемое подразделение для поиска: '+scores[0][1].name
    return question
