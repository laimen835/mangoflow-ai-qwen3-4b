"""Return exact document destinations already present in retrieved official text."""
import re
from urllib.parse import urlsplit


def exact_fact_answer(question, hits):
    """Copy a clearly labelled diploma issuer instead of paraphrasing it away."""
    if 'диплом' not in question.casefold() or not re.search(r'како|образц|выда[её]т',question,re.I):
        return None
    for index,hit in enumerate(hits,1):
        match=re.search(r'Диплом\s+(?:ФГБОУ|ФГАОУ)\s+ВО\s+«[^»]+»',hit.content,re.I)
        if match:
            value=re.sub(r'\s+',' ',match.group(0))
            return f'В опубликованном источнике указан {value} [{index}].'
    return None


def navigation_answer(question, hits):
    text=question.casefold()
    wants_link=any(w in text for w in ('где','найти','скачать','ссылк','посмотреть','открыть'))
    if not wants_link:
        return None
    if any(w in text for w in ('общежит','проживан')):
        fragment='o-nalichii-obshchezhitiya'
        label='Сведения об общежитии и количестве мест'
    elif 'документ' in text and any(w in text for w in ('поступ','абитуриент','перечень')):
        fragment='Perechen_dokum_'
        label='Перечень документов для поступления'
    elif any(w in text for w in ('медицин','медосмотр')):
        fragment='informatsiya-o-neobkhodimosti-prokhozhdeniya'
        label='Сведения о необходимости медицинского осмотра'
    else:
        return None
    requested_years=re.findall(r'\b20\d{2}\b',question)
    for index,hit in enumerate(hits,1):
        for url in re.findall(r'https://ncsa\.ru/[^\s)<>]+',hit.content):
            if fragment not in url:
                continue
            years=re.findall(r'20\d{2}',url)
            if requested_years and years and not any(y in requested_years for y in years):
                continue
            if urlsplit(url).hostname!='ncsa.ru':
                continue
            year=f" ({years[-1]} год)" if years else ''
            return f'{label}{year}:\n{url} [{index}].\nСверьте подробности с оригиналом документа.'
    return None
