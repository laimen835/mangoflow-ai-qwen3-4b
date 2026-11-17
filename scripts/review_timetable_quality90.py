"""Apply an explicit source-hash-bound cell review to the isolated candidate DB."""
import argparse, asyncio, hashlib, json
from pathlib import Path
from sqlalchemy import select
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from src.core.config import settings
from src.core.db.models import Document
from src.core.rag.timetable import extract_page, VERSION


async def main(args):
    import pymupdf
    review = json.loads(Path(args.review).read_text(encoding='utf-8'))
    source = Path(args.pdf)
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    if digest != review['source_sha256'] or review['version'] != VERSION:
        raise ValueError('Review does not match the source or extractor version')
    records, excluded = [], []
    with pymupdf.open(source) as pdf:
        for page in review['pages']:
            result = extract_page(pdf[page - 1])
            records.extend(result['records']); excluded.extend(result['excluded'])
    # Approval is tied to every extracted field, not just the number of rows.
    canonical = json.dumps(records, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    if hashlib.sha256(canonical.encode()).hexdigest() != review['records_sha256']:
        raise ValueError('Extracted cells differ from the visually reviewed snapshot')
    engine = create_async_engine(make_url(settings.database_url).set(database='rag_quality90_candidate'))
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as db:
            doc = await db.scalar(select(Document).where(Document.id == review['document_id']))
            if not doc or doc.source_url != review['source_url'] or doc.source_type != 'ncsa_pdf':
                raise ValueError('Candidate source differs from the reviewed source')
            doc.meta = {**doc.meta, 'reviewed_timetable': {
                'version': VERSION, 'text_verified': True, 'source_sha256': digest,
                'content_hash': doc.content_hash, 'period': review['period'],
                'period_page': review['period_page'], 'records': records, 'excluded': excluded}}
            await db.commit()
            print(f'Candidate only: {len(records)} reviewed cells; {len(excluded)} exclusions.')
    finally:
        await engine.dispose()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--pdf', required=True); parser.add_argument('--review', required=True)
    asyncio.run(main(parser.parse_args()))
