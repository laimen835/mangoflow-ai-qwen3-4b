"""Crawl manually; resume by default, revalidate known resources with --refresh."""
import argparse
import asyncio
import json
from pathlib import Path

from src.core.config import settings
from src.core.rag.crawl_policy import CrawlConfig
from src.core.rag.deep_crawler import DeepCrawler


async def run(args):
    config = CrawlConfig(seeds=tuple(args.seed or ['https://ncsa.ru/institutes/137/']),
                         max_pages=args.max_pages,max_documents=args.max_documents,max_depth=args.max_depth,
                         organization=args.organization, knowledge_base=args.knowledge_base,
                         delay=args.delay, render=args.render)
    if args.index:
        from sqlalchemy.engine import make_url
        if not args.database_name or make_url(settings.database_url).database != args.database_name:
            raise ValueError('--index requires --database-name matching the configured database')
        from tokenizers import Tokenizer
        tokenizer = Tokenizer.from_file(str(Path(args.tokenizer)))
        from src.core.db.session import AsyncSessionLocal, engine
        from src.core.rag.embeddings import build_embedder
        try:
            async with AsyncSessionLocal() as db:
                crawler = DeepCrawler(config,args.folder,db=db,embedder=build_embedder(settings),settings=settings,
                                      tokenizer=tokenizer,office_executable=args.office_executable)
                report = await crawler.run(refresh=args.refresh)
        finally:
            await engine.dispose()
    else:
        report = await DeepCrawler(config,args.folder,settings=settings,office_executable=args.office_executable).run(refresh=args.refresh)
    print(json.dumps({k:v for k,v in report.items() if k not in {'documents','events','errors'}},ensure_ascii=False,indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--folder', required=True)
    parser.add_argument('--seed',action='append')
    parser.add_argument('--organization',default='ncsa')
    parser.add_argument('--knowledge-base',default='public-academy')
    parser.add_argument('--max-pages',type=int,default=200)
    parser.add_argument('--max-documents',type=int,default=100)
    parser.add_argument('--max-depth',type=int,default=20)
    parser.add_argument('--delay',type=float,default=1)
    parser.add_argument('--render',action='store_true')
    parser.add_argument('--refresh',action='store_true')
    parser.add_argument('--index',action='store_true')
    parser.add_argument('--database-name')
    parser.add_argument('--tokenizer',default='D:/Codex-RAG-runtime/scholarship-pilot/nomic-tokenizer/tokenizer.json')
    parser.add_argument('--office-executable',default='')
    asyncio.run(run(parser.parse_args()))


if __name__=='__main__':
    main()
