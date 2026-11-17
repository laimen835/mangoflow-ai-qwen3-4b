"""One frozen end-to-end control run. No automatic keyword accuracy score.

All 70 questions remain in the denominator, including five known source gaps.
The report records raw replies for subsequent human grounding/completeness audit.
"""
import argparse, asyncio, hashlib, json, time
from pathlib import Path
import httpx
from sqlalchemy import select
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from src.core.config import settings
from src.core.db.models import Document, Chunk


def source_tree_hash():
    files = {str(p).replace('\\', '/'): hashlib.sha256(p.read_bytes()).hexdigest()
             for p in sorted(Path('src').rglob('*.py'))}
    return hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()


async def corpus_hash():
    engine = create_async_engine(make_url(settings.database_url).set(database='rag_quality90_candidate'))
    docs = []
    try:
        async with async_sessionmaker(engine)() as db:
            for d in (await db.scalars(select(Document).order_by(Document.id))).all():
                chunks = (await db.scalars(select(Chunk).where(Chunk.document_id == d.id)
                                           .order_by(Chunk.chunk_index))).all()
                docs.append({'id': d.id, 'title': d.title, 'url': d.source_url, 'type': d.source_type,
                    'status': d.status.value, 'meta': d.meta, 'content_hash': d.content_hash,
                    'chunks': [{'id': c.id, 'index': c.chunk_index, 'text': c.content, 'meta': c.meta} for c in chunks]})
    finally:
        await engine.dispose()
    return hashlib.sha256(json.dumps(docs, ensure_ascii=False, sort_keys=True, indent=2).encode()).hexdigest()


def main(args):
    target = Path(args.report)
    if target.exists(): raise ValueError('Refusing to overwrite a control run')
    freeze = json.loads(Path(args.freeze).read_text(encoding='utf-8'))
    if source_tree_hash() != freeze['source_tree_sha256']:
        raise ValueError('API source changed after freeze')
    if asyncio.run(corpus_hash()) != freeze['corpus_sha256']:
        raise ValueError('Candidate corpus changed after freeze')
    source = Path(args.cases)
    dataset = json.loads(source.read_text(encoding='utf-8'))
    if dataset.get('holdout_consumed') and not args.regression:
        raise ValueError('This control set has already been inspected; use --regression or a fresh frozen dataset')
    cases = [q for q in dataset['cases'] if q['split'] == 'holdout']
    if len(cases) != 70 or any(q['gold_status'] == 'requires_source_review' for q in cases):
        raise ValueError('Control must contain 70 source-audited questions')
    target.parent.mkdir(parents=True, exist_ok=True)
    report = {'split': 'regression' if args.regression else 'holdout', 'denominator': 70, 'required_correct': 63,
        'frozen_source_hash': freeze['source_tree_sha256'], 'frozen_corpus_hash': freeze['corpus_sha256'],
        'source_audited_dataset_hash': hashlib.sha256(source.read_bytes()).hexdigest(),
        'known_source_gap_ids': [q['id'] for q in cases if q['gold_status'] == 'insufficient_source'],
        'accuracy': None, 'human_review': 'pending', 'completed': 0, 'results': []}
    with httpx.Client(base_url=args.base_url, timeout=240, trust_env=False) as client:
        client.get('/health').raise_for_status()
        for case in cases:
            start = time.perf_counter(); first = None; response = None; error = None; deltas = []
            try:
                with client.stream('POST', '/api/v1/chat/stream', json={'message': case['question']}) as stream:
                    stream.raise_for_status()
                    for line in stream.iter_lines():
                        if not line: continue
                        event = json.loads(line)
                        if event['type'] == 'delta':
                            if first is None: first = time.perf_counter() - start
                            deltas.append(event['text'])
                        elif event['type'] == 'done':
                            response = event['data']
                            if first is None: first = time.perf_counter() - start
                        elif event['type'] == 'error': raise RuntimeError(event['message'])
                if response is None: raise RuntimeError('No final response')
            except Exception as exc:
                error = type(exc).__name__ + ': ' + str(exc)
            row = {'id': case['id'], 'topic': case['topic'], 'question': case['question'],
                'gold_status': case['gold_status'], 'answer': response, 'error': error,
                'first_text_seconds': round(first, 3) if first is not None else None,
                'full_seconds': round(time.perf_counter() - start, 3), 'delta_count': len(deltas),
                'human_review': 'pending'}
            report['results'].append(row); report['completed'] = len(report['results'])
            target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
            print(f"{report['completed']}/70 id={case['id']} {case['topic']} {row['full_seconds']} "
                  + ('ERROR' if error else 'recorded'), flush=True)
    report['source_unchanged_after_run'] = source_tree_hash() == freeze['source_tree_sha256']
    report['corpus_unchanged_after_run'] = asyncio.run(corpus_hash()) == freeze['corpus_sha256']
    report['run_complete'] = True
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--base-url', default='http://127.0.0.1:8002')
    parser.add_argument('--cases', default='tests/eval/quality90_questions.json')
    parser.add_argument('--regression', action='store_true', help='Explicitly label reused cases as regression')
    parser.add_argument('--freeze', required=True); parser.add_argument('--report', required=True)
    main(parser.parse_args())
