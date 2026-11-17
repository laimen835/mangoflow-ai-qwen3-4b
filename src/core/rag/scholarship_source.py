"""Bounded grants collection. No recursive university crawl or live chat browsing."""
from __future__ import annotations
import hashlib
import re
from urllib.parse import urljoin,urlsplit,urlunsplit,unquote
from bs4 import BeautifulSoup
import httpx

GRANTS='https://ncsa.ru/sveden/grants/'
FORMATS={'.pdf','.docx','.xlsx','.doc','.xls'}


def official_url(address,base=GRANTS):
    p=urlsplit(urljoin(base,address))
    if p.scheme not in {'https','http'} or p.hostname not in {'ncsa.ru','www.ncsa.ru'} or p.username or p.password or p.port not in {None,80,443}:
        raise ValueError('Not an official source URL')
    return urlunsplit(('https','ncsa.ru',p.path,p.query,''))


def document_url(url):
    p=urlsplit(official_url(url))
    if '..' in unquote(p.path).split('/') or '\\' in unquote(p.path):return False
    return p.path.startswith(('/upload/','/institutes/spk/','/sveden/','/students/social/')) and any(p.path.lower().endswith(s) for s in FORMATS)


async def fetch_official(client,url,*,max_bytes=20*1024*1024):
    url=official_url(url)
    for _ in range(6):
        if url!=GRANTS and not document_url(url):raise ValueError('URL outside grants document boundary')
        async with client.stream('GET',url) as response:
            if response.is_redirect:
                url=official_url(response.headers['location'],url);continue
            response.raise_for_status()
            body=bytearray()
            async for part in response.aiter_bytes():
                body.extend(part)
                if len(body)>max_bytes:raise ValueError('Document exceeds 20 MB')
            return bytes(body),url,response.headers.get('content-type','')
    raise ValueError('Too many redirects')


def discover_grants(html):
    soup=BeautifulSoup(html,'html.parser');found={}
    for a in soup.select('a[href]'):
        label=a.get_text(' ',strip=True)
        try:url=official_url(a['href'])
        except ValueError:continue
        if not document_url(url):continue
        # General housing/admission documents are outside this pilot.
        if not re.search('стипен|материальн.*(?:помощ|поддерж)|социальн.*поддерж|обеспечение',label,re.I):continue
        if re.search('приказ.*создани.*комисси',label,re.I) and not re.search('СПК|колледж',label,re.I):continue
        found.setdefault(url,label)
    return sorted(found.items(),key=lambda p:(0 if re.search('стипендиальном обеспечении|материальной помощи',p[1],re.I) else 1,p[0]))[:10]


async def collect_grants(directory):
    """Save an immutable input snapshot before extraction/evaluation."""
    import json
    from datetime import datetime,timezone
    from pathlib import Path
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=True)
    manifest=directory/'manifest.json'
    if manifest.exists():raise ValueError('Snapshot exists; reuse it rather than overwriting evidence')
    items=[];errors=[]
    async with httpx.AsyncClient(timeout=60,follow_redirects=False,trust_env=False) as client:
        body,url,_=await fetch_official(client,GRANTS)
        (directory/'grants.html').write_bytes(body)
        items.append({'url':url,'title':'Стипендии и меры поддержки обучающихся','file':'grants.html','sha256':hashlib.sha256(body).hexdigest(),'parent':GRANTS})
        for url,title in discover_grants(body):
            try:
                data,resolved,mime=await fetch_official(client,url)
                suffix=urlsplit(resolved).path.rsplit('.',1)[-1].lower()
                filename=hashlib.sha256(url.encode()).hexdigest()[:16]+'.'+suffix
                (directory/filename).write_bytes(data)
                items.append({'url':url,'resolved_url':resolved,'title':title,'file':filename,'sha256':hashlib.sha256(data).hexdigest(),'parent':GRANTS,'mime':mime})
            except Exception as exc:errors.append({'url':url,'reason':str(exc)})
    report={'fetched_at':datetime.now(timezone.utc).isoformat(),'items':items,'errors':errors}
    manifest.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    return report
