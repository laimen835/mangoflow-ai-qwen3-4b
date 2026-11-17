"""Read one missing department per institute; import only with explicit --ingest into pilot DB."""
import argparse
import asyncio
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path

import httpx
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine,async_sessionmaker
from src.core.config import settings
from src.core.rag.embeddings import build_embedder
from src.core.rag.chunker import RecursiveChunker,TextChunk
from src.core.rag.ingest import ingest_text
from src.core.rag.ncsa_source import allowed_url,canonical_url,extract_page
from src.core.services.academy_scope import linked_department_scope,unit_for_url


async def main(import_documents=False):
    record=json.loads(Path('D:/Codex-RAG-runtime/academy-site-current.json').read_text())
    folder=Path(record['folder']);cache=folder/'departments';cache.mkdir(exist_ok=True)
    audit=json.loads((folder/'coverage-audit.json').read_text())
    selected=[];seen=set()
    for item in audit['discovered'].values():
        unit=unit_for_url(item['parent'])
        if (unit and unit.key not in seen and not item['in_snapshot']
                and '/about_the_university/structure/chairs/' in item['url']):
            selected.append(item);seen.add(unit.key)
    sources=[]
    async with httpx.AsyncClient(timeout=30,follow_redirects=False,trust_env=False) as client:
        for item in selected[:10]:
            url=item['url']
            if not allowed_url(url):raise ValueError('Unexpected source URL')
            response=await client.get(url)
            # Do not silently follow a redirect to another institution or host.
            response.raise_for_status()
            if response.is_redirect:raise ValueError('Review redirect before import')
            if len(response.content)>5*1024*1024:raise ValueError('HTML too large')
            title,text,links=extract_page(response.text,url)
            if len(text)<100:raise ValueError('Not enough source text')
            scope=linked_department_scope(url,[item])
            label=item['title'].split('<',1)[0].strip()
            title=(label if label.casefold().startswith('кафедра') else 'Кафедра «'+label+'»')+' — '+scope['unit_name']
            name=hashlib.sha256(url.encode()).hexdigest()[:16]
            (cache/(name+'.html')).write_bytes(response.content)
            (cache/(name+'.txt')).write_text(text,encoding='utf-8')
            sources.append({'url':url,'title':title,'text':text,'meta':{
                **scope,'linked_from':[item['parent']],'department_name':label,
                'fetched_at':datetime.now(timezone.utc).isoformat(),'extraction':'html-main-v1',
                'source_sha256':hashlib.sha256(response.content).hexdigest()}})
            print('READ',title,len(text),'chars',flush=True)
    (folder/'department-materials.json').write_text(json.dumps(sources,ensure_ascii=False,indent=2),encoding='utf-8')
    if not import_documents:return
    if make_url(settings.database_url).database!=record['candidate_database']:
        raise RuntimeError('Refusing to write outside the isolated candidate DB')
    engine=create_async_engine(settings.database_url);sessions=async_sessionmaker(engine,expire_on_commit=False)
    try:
        async with sessions() as db:
            for source in sources:
                header=source['title']+'\nНавигация сайта: '+source['meta']['linked_from'][0]+'\n'
                body_chunks=RecursiveChunker(chunk_size=max(300,settings.chunk_size-len(header)),
                                              chunk_overlap=settings.chunk_overlap).split(source['text'])
                chunks=[TextChunk(header+c.content,c.index,{**c.meta,'navigation_heading':source['title'],
                    'department_name':source['meta']['department_name']}) for c in body_chunks]
                result=await ingest_text(db,title=source['title'],text=source['text'],source_type='ncsa_web',
                    source_url=source['url'],external_id=source['url'],meta=source['meta'],
                    embedder=build_embedder(settings),settings=settings,prepared_chunks=chunks,
                    processing_version='academy-department-v2:'+hashlib.sha256(header.encode()).hexdigest()[:16])
                print('IMPORTED',source['url'],result['chunks_created'],flush=True)
    finally:await engine.dispose()


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--ingest',action='store_true');args=parser.parse_args()
    asyncio.run(main(args.ingest))
