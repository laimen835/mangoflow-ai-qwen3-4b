"""Stage a reviewed academy aggregate table; it stays excluded from SPK RAG."""
import argparse, asyncio, json
from pathlib import Path
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from src.core.config import settings
from src.core.rag.embeddings import build_embedder
from src.core.rag.ingest import ingest_text
from src.core.rag.reviewed_web_table import extract_reviewed_table, digest, VERSION
from src.core.rag.scholarship_source import official_url


async def main(args):
    review = json.loads(Path(args.review).read_text(encoding='utf-8'))
    if review.get('scope') != 'academy_general':
        raise ValueError('This import is limited to academy aggregate facts')
    facts = extract_reviewed_table(Path(args.html).read_bytes(), review)
    url = official_url(review['source_url'])
    text = review['heading'] + '\nОбщие данные академии; не квота СПК и не свободные места.\n'
    text += '\n'.join(' | '.join(f'{k}: {v}' for k, v in row.items()) for row in facts['rows'])
    text += '\n' + '\n'.join(link['title'] + ': ' + link['url'] for link in facts['links'])
    meta = {'review_required': True, 'source_sha256': review['source_sha256'],
            'extraction_version': VERSION, 'fetched_at': review['fetched_at'],
            'review': {'scope': 'academy_general', 'text_verified': True, 'topic': 'hostel',
                       'facts_sha256': digest(facts), 'table_sha256': review['table_sha256'],
                       'currency': 'published_snapshot_not_live_availability'}, 'public_facts': facts}
    engine = create_async_engine(make_url(settings.database_url).set(database='rag_quality90_candidate'))
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as db:
            result = await ingest_text(db, title=review['heading'], text=text,
                source_type='ncsa_reviewed_web', source_url=url, external_id=url + '#hostel-statistics',
                meta=meta, embedder=build_embedder(settings), settings=settings,
                processing_version=VERSION + ':' + digest(review))
            print('Candidate only; general SPK retrieval remains excluded:', result)
    finally:
        await engine.dispose()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--html', required=True); parser.add_argument('--review', required=True)
    asyncio.run(main(parser.parse_args()))
