"""Index previously fetched public HTML into an explicitly bound candidate job.

Reads only crawler snapshots, never evaluation questions/answers. Fetch times are
retained; reprocessing does not claim a new site check.
"""
import asyncio
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import unquote,urlsplit

from sqlalchemy.engine import make_url
from tokenizers import Tokenizer
from src.core.config import settings
from src.core.db.session import AsyncSessionLocal,engine
from src.core.rag.crawl_policy import CrawlConfig,document_suffix
from src.core.rag.deep_crawler import DeepCrawler,Manifest,atomic_json,job_lock,validate_knowledge_binding
from src.core.rag.embedding_cache import CheckpointEmbeddings,checkpoint_identity
from src.core.rag.embeddings import build_embedder
from src.core.rag.knowledge_extract import VERSION,html_content,full_text,needs_source_review
from src.core.services.academy_scope import scope_metadata


async def run(args):
    url=make_url(settings.database_url)
    if url.database!=args.database_name or url.database!='rag_deep_candidate' or url.port!=55433:
        raise ValueError('Expected isolated candidate on port 55433')
    source=json.loads(Path(args.source_report).read_text(encoding='utf-8'))
    folder=Path(args.folder)
    config=CrawlConfig()
    binding=hashlib.sha256(url.render_as_string(hide_password=False).encode()).hexdigest()
    tokenizer=Tokenizer.from_file(args.tokenizer)
    with job_lock(folder/'crawl.lock'):
        manifest=Manifest(folder/'manifest.sqlite3',config,binding)
        checkpoint=None
        try:
            async with AsyncSessionLocal() as db:
                await validate_knowledge_binding(db,config)
                identity=await checkpoint_identity(db,settings)
                await db.rollback()
                checkpoint=CheckpointEmbeddings(build_embedder(settings),folder/'embeddings.sqlite3',identity)
                crawler=DeepCrawler(config,folder,db=db,embedder=checkpoint,settings=settings,tokenizer=tokenizer)
                for item in source['documents']:
                    resource=item['url']
                    if config.canonical(resource)!=resource or item['state']!='extracted':
                        raise ValueError('Snapshot leaves the source boundary or was not extracted')
                    if document_suffix(resource) or 'html' not in (item.get('content_type') or ''):
                        raise ValueError('This importer accepts only captured HTML pages')
                    body=Path(item['original_file']).read_bytes()
                    digest=hashlib.sha256(body).hexdigest()
                    if digest!=item['raw_sha256']:
                        raise ValueError('Original snapshot digest mismatch')
                    title,blocks,_,dates=html_content(body,resource,config)
                    text=full_text(blocks)
                    text_digest=hashlib.sha256(text.encode()).hexdigest()
                    original=folder/'originals'/(digest+'.html')
                    original.write_bytes(body)
                    text_file=folder/'text'/(text_digest+'.txt')
                    blocks_file=folder/'text'/(text_digest+'.blocks.json')
                    text_file.write_text(text,encoding='utf-8')
                    atomic_json(blocks_file,[asdict(b) for b in blocks])
                    metadata={**scope_metadata(resource,body.decode('utf-8')),**dates,
                        'source':'ncsa_academy','crawl_scope':config.scope,'organization_key':config.organization,
                        'knowledge_base_key':config.knowledge_base,'document_type':'html','category':'institutes',
                        'fetched_at':item['checked_at'],'raw_sha256':digest,'extraction':'html',
                        'extraction_version':VERSION,'review_policy':'source_text_v1',
                        'review_required':needs_source_review(title,resource,{'ocr':False}),
                        'full_text_file':str(text_file),'blocks_file':str(blocks_file)}
                    result=await crawler.ingest({'url':resource,'suffix':''},title,blocks,metadata,original)
                    manifest.add(resource,title,depth=1)
                    manifest.update(resource,state=result['state'],document_id=result['document_id'],raw_hash=digest,
                        text_hash=text_digest,checked_at=item['checked_at'],http_status=item['http_status'],error=None,
                        data={**result,'title':title,'text_file':str(text_file),'blocks_file':str(blocks_file),
                              'text_sha256':text_digest,'original_file':str(original),'unit_key':metadata['unit_key'],
                              'content_type':'text/html','extraction':'html','text_characters':len(text)})
                    print('Indexed public snapshot',resource,result['status'] if 'status' in result else result['state'],flush=True)
                rows=manifest.db.execute('SELECT * FROM resources WHERE document_id IS NOT NULL').fetchall()
                for row in rows:
                    name=unquote(urlsplit(row['url']).path).rstrip('/').rsplit('/',1)[-1]
                    if re.fullmatch(r'[\w.+-]+@[\w.-]+\.[a-zA-Z]{2,}',name):
                        await crawler.archive(row)
                        manifest.update(row['url'],state='retired_invalid_url',error=None)
                        manifest.event(row['url'],'retired_invalid_email_url',{})
        finally:
            if checkpoint:
                checkpoint.close()
            atomic_json(folder/'report.json',manifest.report())
            manifest.db.close()
            await engine.dispose()


def main():
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-report',required=True)
    parser.add_argument('--folder',required=True)
    parser.add_argument('--database-name',required=True)
    parser.add_argument('--tokenizer',default='D:/Codex-RAG-runtime/scholarship-pilot/nomic-tokenizer/tokenizer.json')
    asyncio.run(run(parser.parse_args()))


if __name__=='__main__':
    main()
