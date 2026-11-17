"""Record the frozen DEVELOPMENT split end to end. Never runs the holdout.

No keyword pass rate: a human must assess completeness and source entailment.
Diagnostics and model outputs stay outside Git in the specified local report.
"""
import argparse, json, time
from pathlib import Path
import httpx
from src.core.config import settings


def main(args):
    cases = json.loads(Path('tests/eval/quality90_questions.json').read_text(encoding='utf-8'))['cases']
    if args.regression and not args.ids:
        raise ValueError('Specify known case IDs for a labelled regression probe')
    cases = cases if args.regression else [q for q in cases if q['split'] == 'development']
    if args.ids:
        ids = {int(value) for value in args.ids.split(',')}
        cases = [q for q in cases if q['id'] in ids]
        if len(cases) != len(ids):
            raise ValueError('Unknown IDs or holdout IDs without explicit --regression')
    rows = []; target = Path(args.report); target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        raise ValueError('Choose a new report; do not overwrite prior outputs')
    with httpx.Client(base_url=args.base_url, timeout=180, trust_env=False) as client:
        deadline = time.monotonic() + 15
        while True:
            try:
                ready = client.get('/health', timeout=2)
                ready.raise_for_status(); break
            except httpx.HTTPError:
                if time.monotonic() >= deadline:
                    raise RuntimeError('Candidate API did not start; no evaluation questions were sent')
                time.sleep(0.25)
        for case in cases:
            query = {'message': case['question']}; start = time.perf_counter(); first = None
            response = None; error = None; diagnostics = None; deltas = []
            try:
                with client.stream('POST', '/api/v1/chat/stream', json=query) as stream:
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
                        elif event['type'] == 'error':
                            raise RuntimeError(event['message'])
                if response is None: raise RuntimeError('Missing final response')
                full = time.perf_counter() - start
                if response.get('usage', {}).get('route') == 'rag':
                    debug = client.post('/api/v1/admin/search/debug', json={'query': case['question']},
                                        headers={'X-Admin-Key': settings.admin_api_key})
                    debug.raise_for_status(); diagnostics = debug.json()
            except Exception as exc:
                full = time.perf_counter() - start; error = type(exc).__name__ + ': ' + str(exc)
            rows.append({'id': case['id'], 'topic': case['topic'], 'question': case['question'],
                         'gold_status': case['gold_status'], 'answer': response, 'diagnostics': diagnostics,
                         'first_text_seconds': round(first, 3) if first is not None else None,
                         'full_seconds': round(full, 3), 'delta_count': len(deltas), 'error': error,
                         'human_review': 'pending'})
            target.write_text(json.dumps({'split': 'regression' if args.regression else 'development', 'holdout_run': False,
                'coverage_claim': None, 'completed': len(rows), 'results': rows}, ensure_ascii=False, indent=2), encoding='utf-8')
            print(case['id'], case['topic'], round(full, 3), 'ERROR' if error else 'recorded', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--base-url', default='http://127.0.0.1:8002')
    parser.add_argument('--regression', action='store_true', help='Explicitly label already inspected cases as regression')
    parser.add_argument('--report', required=True); parser.add_argument('--ids')
    main(parser.parse_args())
