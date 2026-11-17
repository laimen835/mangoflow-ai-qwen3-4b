"""Ingest reviewed PDF clauses into the candidate only; both vector profiles kept."""
import argparse, asyncio, hashlib, json
from pathlib import Path
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from tokenizers import Tokenizer
from src.core.config import settings
from src.core.rag.reviewed_documents import load_reviewed_pdf, VERSION
from src.core.rag.structured_documents import structural_chunks, render_blocks
from src.core.rag.ingest import ingest_text
from src.core.rag.embeddings import build_embedder
from src.core.rag.scholarship_source import official_url
from src.core.db.models import Document, DocumentStatus


async def main(args):
    review = json.loads(Path(args.review).read_text(encoding='utf-8'))
    source_url = official_url(review['source_url'])
    blocks, method = load_reviewed_pdf(Path(args.pdf).read_bytes(), review)
    tokenizer = Tokenizer.from_file(args.tokenizer)
    chunks = structural_chunks(blocks, review['title'], tokenizer)
    for chunk in chunks:
        span = next(s for s in review['ranges'] if s['section'] == chunk.meta['section'])
        block = next(b for b in blocks if b.section == span['section'])
        chunk.meta.update(pages=list(range(span['start_page'], span['end_page'] + 1)),
                          source_sha256=review['source_sha256'], extraction_version=VERSION,
                          reviewed_section_text=block.text)
    meta = {'review_required': True, 'source_sha256': review['source_sha256'],
            'linked_from': [official_url(review['parent'])], 'fetched_at': review['fetched_at'],
            'extraction': method, 'extraction_version': VERSION,
            'review': {'scope': review['scope'], 'scope_quote': review['scope_quote'],
                       'scope_page': review['scope_page'], 'text_verified': True,
                       'period': review['period'], 'review_version': VERSION,
                       **{key: review[key] for key in ['period_basis', 'period_quote', 'period_page', 'currency', 'document_period'] if key in review},
                       'section_hashes': {b.section: hashlib.sha256(b.text.encode()).hexdigest() for b in blocks}}}
    url = make_url(settings.database_url).set(database='rag_quality90_candidate')
    engine = create_async_engine(url)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as db:
            source_type, external_id = 'ncsa_reviewed', source_url
            if args.replace_document is not None:
                existing = await db.get(Document, args.replace_document)
                if (not existing or existing.status != DocumentStatus.INDEXED
                        or existing.source_type != 'ncsa_pdf' or existing.source_url != source_url
                        or existing.external_id != source_url):
                    raise ValueError('Replacement must target the same indexed official PDF in the candidate')
                source_type, external_id = existing.source_type, existing.external_id
                meta = {**existing.meta, **meta}
            result = await ingest_text(db, title=review['title'], text=render_blocks(blocks),
                source_type=source_type, source_url=source_url, external_id=external_id,
                meta=meta, prepared_chunks=chunks, settings=settings, embedder=build_embedder(settings),
                processing_version=VERSION + ':section-context-v1:' + hashlib.sha256(
                    (Path(args.review).read_text(encoding='utf-8') + tokenizer.to_str()).encode()).hexdigest())
            print('Candidate only:', result)
    finally:
        await engine.dispose()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    for name in ['pdf', 'review', 'tokenizer']:
        parser.add_argument('--' + name, required=True)
    parser.add_argument('--replace-document', type=int,
                        help='Replace only the matching original PDF in the isolated candidate database')
    asyncio.run(main(parser.parse_args()))
