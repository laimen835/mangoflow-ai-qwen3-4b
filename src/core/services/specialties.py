"""Read explicit specialty fields from the college source, never model memory."""
import re

CODE=re.compile(r'\b\d{2}\.\d{2}\.\d{2}\b')


def reconstruct(chunks):
    text=''
    for chunk in chunks:
        content=chunk.content
        overlap=0
        if text:
            for size in range(min(256,len(text),len(content)),15,-1):
                if text.endswith(content[:size]):overlap=size;break
        text+=('\n' if text and not overlap else '')+content[overlap:]
    return re.sub(r'\s+',' ',text).strip()


def specialty_fields(chunks):
    text=reconstruct(chunks);records={}
    pattern=re.compile(r'\((?P<code>\d{2}\.\d{2}\.\d{2})\)\s*Квалификация:\s*(?P<qualification>.{2,200}?)\s*Срок обучения:\s*(?P<years>\d+)\s*г\.?\s*(?P<months>\d+)\s*мес\.?',re.I)
    for match in pattern.finditer(text):
        before=text[max(0,match.start()-180):match.start()].strip()
        # The website marks cards with emoji. Keep the actual source wording
        # after that boundary; do not derive a program name from its code.
        pieces=re.split(r'[^\w\s(),./«»—–-]+',before)
        name=pieces[-1].strip(' .') if pieces else ''
        if not name or len(name)>140:name=None
        code=match['code']
        if CODE.search(match['qualification']) or re.search('Квалификац|Объект',match['qualification'],re.I):continue
        evidence=next((c.id for c in chunks if code in c.content and 'Квалификац' in c.content),None)
        record={'code':code,'name':name,'qualification':match['qualification'].strip(),
            'years':int(match['years']),'months':int(match['months']),'chunk_id':evidence,'quote':match.group(),'conflicts':{}}
        old=records.get(code)
        if old:
            if old['qualification']!=record['qualification']:
                old['conflicts']['qualification']=list(dict.fromkeys(old['conflicts'].get('qualification',[old['qualification']])+[record['qualification']]))
            if (old['years'],old['months'])!=(record['years'],record['months']):
                old['conflicts']['duration']=list(dict.fromkeys(old['conflicts'].get('duration',[(old['years'],old['months'])])+[(record['years'],record['months'])]))
            continue
        records[code]=record
    return records


def specialty_request(question):
    code=CODE.search(question)
    if not code:return None
    if not re.search(r'квалифик|(?:срок|продолжительност|длительност).*обуч|сколько.{0,30}уч[аи]|как долго.{0,20}уч|называ.{0,30}специальн|специальност.{0,30}код|что.{0,30}специальност|назван.{0,30}(?:направлен|специальн)',question,re.I):return None
    return code.group()


def format_specialty(record,question):
    parts=[]
    if not record['name'] and re.search(r'называ|назван|что',question,re.I) and not re.search('квалифик|срок|сколько',question,re.I):return None
    if record['name'] and re.search(r'называ|назван|что|специальност',question,re.I):
        parts.append(f"{record['code']} — {record['name']}.")
    if re.search(r'квалифик',question,re.I):
        variants=record.get('conflicts',{}).get('qualification')
        parts.append('На странице встречаются разные формулировки квалификации: '+'; '.join(variants)+'. Требуется уточнение по учебному плану.' if variants else 'Квалификация: '+record['qualification']+'.')
    if re.search(r'срок|сколько|продолжительност|длительност|как долго',question,re.I):
        variants=record.get('conflicts',{}).get('duration')
        parts.append('На странице встречаются разные сроки: '+', '.join(f'{y} г. {m} мес.' for y,m in variants)+'. Уточните базу образования и учебный план.' if variants else f"Срок обучения: {record['years']} г. {record['months']} мес.")
    if not parts:parts=['Квалификация: '+record['qualification']+'.']
    return '\n'.join(parts)+' [1]\nСведения приведены по опубликованной странице СПК.'
