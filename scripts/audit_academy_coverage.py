"""Bounded read-only source audit. Downloads HTML, never updates the DB/index."""
import asyncio
from collections import Counter
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from src.core.rag.ncsa_source import START_PAGES,allowed_url,canonical_url,extract_page
from src.core.services.academy_scope import unit_for_url


async def main():
    record=json.loads(Path('D:/Codex-RAG-runtime/academy-site-current.json').read_text())
    folder=Path(record['folder']);cache=folder/'coverage-html';cache.mkdir(exist_ok=True)
    docs=json.loads((folder/'corpus.json').read_text())
    known={canonical_url(d['url']) for d in docs if d['url']}
    inventory=Counter((d['meta'] or {}).get('unit_key','legacy_unknown') for d in docs)
    report={'checked_at':datetime.now(timezone.utc).isoformat(),'indexed_documents':len(docs),
            'chunks':sum(len(d['chunks']) for d in docs),'unit_inventory':dict(inventory),
            'pages':[],'discovered':{},'errors':[],
            'limitation':'Discovery on bounded start pages, not a complete sitemap or percentage of the entire website.'}
    async with httpx.AsyncClient(timeout=30,follow_redirects=False,trust_env=False) as client:
        for original in list(dict.fromkeys(START_PAGES))[:20]:
            target=original
            try:
                for redirect in range(6):
                    if not allowed_url(target):raise ValueError('Unsafe redirect')
                    response=await client.get(target)
                    if response.is_redirect:
                        target=canonical_url(response.headers.get('location',''),target)
                        if not target:raise ValueError('External redirect')
                    else:break
                else:raise ValueError('Too many redirects')
                response.raise_for_status()
                if len(response.content)>5*1024*1024:raise ValueError('HTML size limit')
                title,text,links=extract_page(response.text,target)
                (cache/(hashlib.sha256(target.encode()).hexdigest()[:16]+'.html')).write_bytes(response.content)
                missing=[]
                for url,label in links:
                    unit=unit_for_url(url)
                    item={'url':url,'title':label,'parent':target,'unit':unit.key if unit else 'academy',
                          'format':Path(urlsplit(url).path).suffix or 'html','in_snapshot':url in known}
                    report['discovered'].setdefault(url,item)
                    if not item['in_snapshot']:missing.append(url)
                report['pages'].append({'url':target,'title':title,'text_chars':len(text),
                                        'direct_links':len(links),'missing_from_snapshot':len(missing)})
                print('AUDIT',target,'links',len(links),'missing',len(missing),flush=True)
            except Exception as exc:
                report['errors'].append({'url':original,'error':type(exc).__name__})
            (folder/'coverage-audit.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    values=list(report['discovered'].values())
    report['summary']={'start_pages_read':len(report['pages']),'unique_direct_links':len(values),
        'links_in_snapshot':sum(v['in_snapshot'] for v in values),'links_missing':sum(not v['in_snapshot'] for v in values),
        'missing_by_format':dict(Counter(v['format'] for v in values if not v['in_snapshot']))}
    (folder/'coverage-audit.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report['summary'],ensure_ascii=False),flush=True)


if __name__=='__main__':asyncio.run(main())
