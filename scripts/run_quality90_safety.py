"""Separate safety probes. Their refusals never increase the 70-question score."""
import argparse, asyncio, json, time
from pathlib import Path
import httpx
from src.core.config import settings
from scripts.run_quality90_control import source_tree_hash, corpus_hash


def main(args):
    freeze = json.loads(Path(args.freeze).read_text(encoding='utf-8'))
    if source_tree_hash() != freeze['source_tree_sha256'] or asyncio.run(corpus_hash()) != freeze['corpus_sha256']:
        raise ValueError('Use the same frozen candidate')
    target = Path(args.report)
    if target.exists(): raise ValueError('Do not overwrite safety probes')
    cases = json.loads(Path('tests/eval/quality90_questions.json').read_text(encoding='utf-8'))['safety_cases']
    rows = []
    with httpx.Client(base_url=args.base_url, timeout=240, trust_env=False) as client:
        for case in cases:
            start = time.perf_counter(); response = None; error = None
            try:
                r = client.post('/api/v1/chat', json={'message': case['question']})
                r.raise_for_status(); response = r.json()
            except Exception as exc: error = type(exc).__name__
            serialized = json.dumps(response, ensure_ascii=False)
            key_exposed = bool(settings.admin_api_key and settings.admin_api_key in serialized)
            rows.append({**case, 'answer': response, 'error': error,
                         'full_seconds': round(time.perf_counter()-start, 3),
                         'active_admin_key_exposed': key_exposed, 'human_review': 'pending'})
            target.write_text(json.dumps({'kind': 'safety_only', 'coverage_credit': 0,
                'completed': len(rows), 'results': rows}, ensure_ascii=False, indent=2), encoding='utf-8')
            print(case['id'], 'recorded' if response else 'ERROR', 'key_exposure' if key_exposed else '', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--base-url', default='http://127.0.0.1:8002')
    parser.add_argument('--freeze', required=True); parser.add_argument('--report', required=True)
    main(parser.parse_args())
