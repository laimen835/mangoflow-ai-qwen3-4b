"""Append visually verified support clauses in the isolated candidate only.

Default is a read-only preview. Frozen PDF/OCR files and original reviews are
never modified. The new review and actual PDF hash are part of reindex identity.
"""
import argparse
import asyncio
import hashlib
import json
from pathlib import Path

from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from tokenizers import Tokenizer

from src.core.config import settings
from src.core.rag.embeddings import build_embedder
from src.core.rag.ingest import ingest_text
from src.core.rag.scholarship_ingest import load_materials
from src.core.rag.scholarship_source import official_url
from src.core.rag.structured_documents import Block, structural_chunks, render_blocks


VERSION = 'reviewed-support-pages-v1'


def merge_reviewed_blocks(item, old_blocks, extracted, old_review, addition):
    if (addition.get('version') != 'visual-transcription-v1'
            or item['sha256'] != addition.get('source_sha256')
            or official_url(item['url']) != official_url(addition['source_url'])
            or old_review.get('text_verified') is not True
            or old_review.get('scope') != 'spo_confirmed'
            or addition.get('scope') != old_review['scope']
            or addition.get('scope_quote') != old_review['scope_quote']
            or addition.get('currency') != old_review['currency']
            or addition.get('document_period') != old_review['document_period']):
        raise ValueError('Additional review does not match the approved source and scope')
    pages = {b['page'] for b in extracted['blocks']}
    blocks = list(old_blocks)
    seen = {(b.page, b.section) for b in blocks}
    for entry in addition['blocks']:
        key = (entry['page'], entry['section'])
        text = entry['text']
        if (key in seen or entry['page'] not in pages or not text.strip()
                or hashlib.sha256(text.encode()).hexdigest() != entry['text_sha256']):
            raise ValueError('Duplicate, changed or invalid reviewed clause')
        seen.add(key)
        blocks.append(Block(text, entry['section'], page=entry['page']))
    if len(blocks) == len(old_blocks):
        raise ValueError('No additional reviewed clauses')
    return blocks


async def main(args):
    addition_path = Path(args.review)
    addition = json.loads(addition_path.read_text(encoding='utf-8'))
    manifest, materials = load_materials(args.snapshot, args.baseline_review)
    matching = [m for m in materials if m[0]['sha256'] == addition['source_sha256']]
    if len(matching) != 1:
        raise ValueError('Expected one frozen original source')
    item, old_blocks, extracted, old_review = matching[0]
    blocks = merge_reviewed_blocks(item, old_blocks, extracted, old_review, addition)
    tokenizer = Tokenizer.from_file(args.tokenizer)
    review = {**old_review, 'additional_review_version': VERSION,
              'additional_review_sha256': hashlib.sha256(addition_path.read_bytes()).hexdigest(),
              'additional_review_method': addition['review_method'],
              'additional_review_pages': sorted({b['page'] for b in addition['blocks']}),
              'section_hashes': {b.section: hashlib.sha256(b.text.encode()).hexdigest() for b in blocks}}
    chunks = structural_chunks(blocks, item['title'], tokenizer)
    sections = {b.section: b for b in blocks}
    for chunk in chunks:
        block = sections[chunk.meta['section']]
        chunk.meta.update(reviewed_section_text=block.text, page=block.page,
                          source_sha256=item['sha256'], extraction_version=VERSION)
    if not args.apply:
        print(f'Preview only: {len(old_blocks)} approved blocks + '
              f'{len(blocks)-len(old_blocks)} visually verified blocks; {len(chunks)} chunks. No database changes.')
        return
    url = make_url(settings.database_url).set(database='rag_quality90_candidate')
    cfg = settings.model_copy(update={'database_url': url.render_as_string(hide_password=False)})
    engine = create_async_engine(url)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as db:
            meta = {'scholarship_material': True, 'review_required': True, 'source': 'ncsa_grants',
                    'source_sha256': item['sha256'], 'linked_from': [item['parent']],
                    'fetched_at': manifest['fetched_at'], 'extraction': extracted['method'] + '+visual-transcription',
                    'extraction_version': VERSION, 'review': review,
                    'source_snapshot': item['file'], 'extraction_error': None}
            signature = hashlib.sha256(json.dumps({'review': review, 'tokenizer': tokenizer.to_str()},
                                                  ensure_ascii=False, sort_keys=True).encode()).hexdigest()
            result = await ingest_text(db, title=item['title'], text=render_blocks(blocks),
                source_type='ncsa_scholarship', source_url=item['url'], external_id=item['url'],
                meta=meta, prepared_chunks=chunks, settings=cfg, embedder=build_embedder(cfg),
                processing_version=VERSION + ':' + signature)
            print('Candidate only:', result)
    finally:
        await engine.dispose()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--snapshot', default='D:/Codex-RAG-runtime/scholarship-pilot/snapshot-v2')
    parser.add_argument('--baseline-review', default='tests/eval/scholarship_review.json')
    parser.add_argument('--review', default='docs/source-reviews/material-support-additional-pages.json')
    parser.add_argument('--tokenizer', default='D:/Codex-RAG-runtime/scholarship-pilot/nomic-tokenizer/tokenizer.json')
    asyncio.run(main(parser.parse_args()))
