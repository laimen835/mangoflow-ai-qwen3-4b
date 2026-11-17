"""Reprocess managed snapshot files without claiming a new source fetch."""
import asyncio
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import sqlite3

from tokenizers import Tokenizer
from sqlalchemy.engine import make_url
from src.core.config import settings
from src.core.db.models import Document
from src.core.db.session import AsyncSessionLocal,engine
from src.core.rag.crawl_policy import CrawlConfig
from src.core.rag.deep_crawler import DeepCrawler,Manifest,atomic_json,job_lock,now
from src.core.rag.embeddings import build_embedder
from src.core.rag.embedding_cache import CheckpointEmbeddings,checkpoint_identity
from src.core.rag.knowledge_extract import VERSION,html_content,file_content,full_text


async def run(args):
    url=make_url(settings.database_url)
    if url.database!=args.database_name or url.database!='rag_deep_candidate' or url.port!=55433:
        raise ValueError('Expected the isolated candidate database on port 55433')
    folder=Path(args.folder)
    config=CrawlConfig()
    binding=hashlib.sha256(url.render_as_string(hide_password=False).encode()).hexdigest()
    tokenizer=Tokenizer.from_file(args.tokenizer)
    results=[]
    with job_lock(folder/'crawl.lock'):
        manifest=Manifest(folder/'manifest.sqlite3',config,binding)
        checkpoint=None
        try:
            rows=manifest.db.execute("SELECT * FROM resources WHERE state='indexed' AND document_id IS NOT NULL ORDER BY rowid").fetchall()
            async with AsyncSessionLocal() as db:
                identity=await checkpoint_identity(db,settings)
                await db.rollback()
                checkpoint=CheckpointEmbeddings(build_embedder(settings),folder/'embeddings.sqlite3',identity)
                crawler=DeepCrawler(config,folder,db=db,embedder=checkpoint,settings=settings,tokenizer=tokenizer)
                for row in rows:
                    doc=await db.get(Document,row['document_id'])
                    if not doc or (doc.meta or {}).get('crawl_scope')!=config.scope:
                        continue
                    old=json.loads(row['data'])
                    metadata=dict(doc.meta or {})
                    original=Path(old['original_file'])
                    body=original.read_bytes()
                    try:
                        if not row['suffix']:
                            title,blocks,_,_=html_content(body,row['url'],config)
                            from src.core.rag.ncsa_source import SEED,extract_page
                            if row['url']==SEED:
                                from src.core.rag.structured_documents import Block
                                title,text,_=extract_page(body.decode('utf-8'),row['url'])
                                blocks=[Block(text,title)]
                        else:
                            title=old['title']
                            blocks,extraction=await asyncio.to_thread(file_content,body,row['suffix'],
                                tesseract=settings.tesseract_cmd,cache=folder,ocr_pages=config.ocr_pages)
                            metadata.update(extraction)
                            metadata['review_required']=bool(metadata.get('review_required') or extraction.get('ocr') or extraction.get('layout_review_required'))
                        text=full_text(blocks)
                        digest=hashlib.sha256(text.encode()).hexdigest()
                        text_file=folder/'text'/(digest+'.txt')
                        blocks_file=folder/'text'/(digest+'.blocks.json')
                        text_file.write_text(text,encoding='utf-8')
                        atomic_json(blocks_file,[asdict(b) for b in blocks])
                        metadata.update(full_text_file=str(text_file),blocks_file=str(blocks_file),extraction_version=VERSION,
                            review_policy='source_text_v1',reindexed_at=now())
                        result=await crawler.ingest(dict(row),title,blocks,metadata,original)
                        manifest.update(row['url'],state=result['state'],text_hash=digest,data={**old,**result,
                            'text_file':str(text_file),'blocks_file':str(blocks_file),'text_sha256':digest,'reindexed_at':now()})
                        results.append({'url':row['url'],**result})
                    except Exception as exc:
                        await db.rollback()
                        results.append({'url':row['url'],'error':type(exc).__name__})
                    if len(results)%10==0:
                        print('Reprocessed',len(results),'/',len(rows),flush=True)
        finally:
            if checkpoint:
                checkpoint.close()
            atomic_json(folder/'report.json',manifest.report())
            atomic_json(folder/'reindex-report.json',{'version':VERSION,'results':results})
            manifest.db.close()
            await engine.dispose()
    print('Reprocessed',len(results),'snapshot records; errors',sum('error' in r for r in results))


def main():
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--folder',required=True)
    parser.add_argument('--database-name',required=True)
    parser.add_argument('--tokenizer',default='D:/Codex-RAG-runtime/scholarship-pilot/nomic-tokenizer/tokenizer.json')
    asyncio.run(run(parser.parse_args()))


if __name__=='__main__':
    main()
