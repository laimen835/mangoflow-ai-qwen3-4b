"""Recover deterministic context for audit without generating new control answers.

Matches IDs and returned snippets; labels mismatches rather than assuming an
exact reconstruction. Requires the same source tree and frozen corpus.
"""
import argparse, asyncio, json
from pathlib import Path
import httpx
from src.core.config import settings
from src.shared.text import truncate
from scripts.run_quality90_control import source_tree_hash, corpus_hash


def main(args):
    report = json.loads(Path(args.control).read_text(encoding='utf-8'))
    if source_tree_hash() != report['frozen_source_hash']:
        raise ValueError('Code differs from control')
    if asyncio.run(corpus_hash()) != report['frozen_corpus_hash']:
        raise ValueError('Corpus differs from control')
    target = Path(args.report)
    if target.exists(): raise ValueError('Do not overwrite an audit replay')
    rows = []
    with httpx.Client(base_url=args.base_url, timeout=40, trust_env=False) as client:
        for row in report['results']:
            answer = row.get('answer')
            if not answer or answer.get('usage', {}).get('route') != 'rag': continue
            response = client.post('/api/v1/admin/search/debug', json={'query': row['question']},
                                   headers={'X-Admin-Key': settings.admin_api_key})
            response.raise_for_status(); replay = response.json(); context = replay['final_context']
            source_ids = [s['chunk_id'] for s in answer['sources']]
            matches = source_ids == [h['chunk_id'] for h in context]
            snippets = matches and all(s.get('snippet') == truncate(h['content'], 300)
                                       for s, h in zip(answer['sources'], context))
            rows.append({'id': row['id'], 'source_ids_match': matches, 'snippets_match': snippets,
                         'replay': replay})
            target.write_text(json.dumps({'limitation': 'Replayed after generation, not a literal request capture. '
                'Code/corpus hashes and source IDs/snippets checked.', 'rows': rows}, ensure_ascii=False, indent=2), encoding='utf-8')
    print('Replayed', len(rows), 'RAG contexts; ID matches', sum(r['source_ids_match'] for r in rows),
          'snippet matches', sum(r['snippets_match'] for r in rows))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--base-url', default='http://127.0.0.1:8002')
    parser.add_argument('--control', required=True); parser.add_argument('--report', required=True)
    main(parser.parse_args())
