"""Collect only directly linked, relevant official files into an isolated snapshot.

Never ingests or approves a document. Applicant name lists and exam results are
not useful for answering general questions and are deliberately not collected.
"""
import asyncio,json,hashlib,re
from pathlib import Path
from urllib.parse import urlsplit
from datetime import datetime,timezone
import httpx
from bs4 import BeautifulSoup
from src.core.rag.scholarship_source import official_url,document_url
from src.core.rag.structured_documents import extract
from dataclasses import asdict
from scripts.quality90_workspace import ROOT
from src.core.rag.source_links import link_title

PARENTS=[
 'https://ncsa.ru/institutes/spk/postupayushchim-v-spk/',
 'https://ncsa.ru/institutes/spk/postupayushchim-v-spk/forma-i-programmy-vstupitelnykh-ispytaniy-tvorcheskoy-napravlennosti.php',
 'https://ncsa.ru/institutes/spk/postupayushchim-v-spk/informatsiya-o-neobkhodimosti-prokhozhdeniya-postupayushchimi-obyazatelnogo-predvaritelnogo-meditsin.php',
 'https://ncsa.ru/sveden/grants/']


async def fetch(client,url):
    for _ in range(6):
        url=official_url(url)
        if url not in PARENTS and not document_url(url):raise ValueError('Outside configured direct-source boundary')
        async with client.stream('GET',url) as response:
            if response.is_redirect:url=official_url(response.headers['location'],url);continue
            response.raise_for_status();data=bytearray()
            async for block in response.aiter_bytes():
                data.extend(block)
                if len(data)>20*1024*1024:raise ValueError('File exceeds 20 MB')
            return bytes(data),url
    raise ValueError('Too many redirects')


async def main():
    folder=ROOT/'materials-v2';folder.mkdir(exist_ok=True)
    target=folder/'manifest.json'
    if target.exists():raise ValueError('Snapshot already exists; do not overwrite source evidence')
    links={};errors=[];pages=[]
    async with httpx.AsyncClient(timeout=60,follow_redirects=False,trust_env=False) as client:
        for parent in PARENTS:
            try:
                data,resolved=await fetch(client,parent)
                filename=hashlib.sha256(parent.encode()).hexdigest()[:16]+'.html';(folder/filename).write_bytes(data)
                pages.append({'url':parent,'file':filename,'sha256':hashlib.sha256(data).hexdigest()})
                soup=BeautifulSoup(data,'html.parser');root=soup.select_one('#ncsa-main .content_container') or soup
                for a in root.select('a[href]'):
                    label=link_title(a)
                    if re.search('списки|результат|поданн.*заявлен|итоги.*испытан',label,re.I):continue
                    if not re.search('перечень документ|правила приема|правила приёма|специальност|уровн.*образован|условия приема|условия приёма|сроки проведен|общежити|проживан|договор.*найма|стипендиальн.*обеспечен|материальн.*помощ|медосмотр|врачей',label,re.I):continue
                    try:url=official_url(a['href'],parent)
                    except ValueError:continue
                    if not document_url(url):continue
                    if re.search('федеральный закон|минпросвещ|постановлени|2019.2020|мониторинг',label,re.I):continue
                    if url not in links:links[url]={'url':url,'title':label,'parent':parent}
            except Exception as exc:errors.append({'url':parent,'reason':str(exc)})
        items=[]
        for item in list(links.values())[:18]:
            try:
                body,resolved=await fetch(client,item['url']);suffix=Path(urlsplit(resolved).path).suffix.lower()
                filename=hashlib.sha256(item['url'].encode()).hexdigest()[:16]+suffix;(folder/filename).write_bytes(body)
                record={**item,'file':filename,'sha256':hashlib.sha256(body).hexdigest(),'review_status':'not_reviewed'}
                try:
                    blocks,method=extract(body,suffix)
                    record['extraction']=method
                    (folder/(filename+'.json')).write_text(json.dumps({'method':method,'blocks':[asdict(b) for b in blocks]},ensure_ascii=False,indent=2),encoding='utf-8')
                except Exception as exc:record['extraction_error']=str(exc)
                items.append(record);print('Saved',filename,item['title'][:90],flush=True)
            except Exception as exc:errors.append({'url':item['url'],'reason':str(exc)})
    report={'checked_at':datetime.now(timezone.utc).isoformat(),'parents':pages,'documents':items,'errors':errors,
            'policy':'Staged files only, no database writes or automatic factual approval.'}
    target.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print('Staged',len(items),'documents;',len(errors),'errors. Production corpus unchanged.',flush=True)


if __name__=='__main__':asyncio.run(main())
