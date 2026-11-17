"""Run the 60 source-grounded regression questions against the local API."""
import json
import argparse
import sys
import time
from pathlib import Path
import httpx
from scripts.quality_cases import CASES, check_answer


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--base-url',default='http://127.0.0.1:8000')
    parser.add_argument('--report',default='logs/quality-results.json');parser.add_argument('--retry-failed',action='store_true');args=parser.parse_args()
    results=[]
    retry_failed=args.retry_failed
    report_path=Path(args.report)
    previous=json.loads(report_path.read_text(encoding='utf-8')) if retry_failed else {}
    prior={r['case']:r for r in previous.get('results',[])}
    with httpx.Client(base_url=args.base_url,timeout=240) as client:
        for index,case in enumerate(CASES,1):
            if retry_failed and prior.get(index,{}).get('passed'):
                results.append(prior[index]);continue
            session=None
            if case.get('setup'):
                setup=client.post('/api/v1/chat',json={'message':case['setup']})
                setup.raise_for_status();session=setup.json()['session_id']
            started=time.monotonic();first=None;answer=None;deltas=[];error=None
            payload={'message':case['question']}
            if session:payload['session_id']=session
            if case.get('group'):payload['group']=case['group']
            try:
                with client.stream('POST','/api/v1/chat/stream',json=payload) as r:
                    r.raise_for_status()
                    for line in r.iter_lines():
                        if not line:continue
                        event=json.loads(line)
                        if event['type']=='delta':
                            if first is None:first=time.monotonic()-started
                            deltas.append(event['text'])
                        if event['type']=='done':
                            answer=event['data']
                            if first is None:first=time.monotonic()-started
                        if event['type']=='error':raise RuntimeError(event['message'])
                if not answer:raise RuntimeError('Missing done event')
                sources=answer.get('sources',[])
                reference_ok=case['reference'] is None or any(case['reference'] in (s.get('url')or'') for s in sources)
                contextual_ok=not case.get('contextual') or answer.get('usage',{}).get('contextual_search')
                passed=check_answer(answer['answer'],case) and reference_ok and bool(contextual_ok)
            except Exception as exc:
                error=str(exc);passed=False
            result={'case':index,**case,'passed':passed,'answer':answer,'error':error,
                    'first_text_seconds':round(first,3) if first is not None else None,
                    'total_seconds':round(time.monotonic()-started,3),'delta_events':len(deltas)}
            if retry_failed and index in prior:
                result['prior_attempts']=prior[index].get('prior_attempts',[])+[prior[index]]
            results.append(result)
            report_path.write_text(json.dumps({'cases':60,'passed':sum(x['passed'] for x in results),'completed':len(results),'results':results,'targeted_retry':retry_failed,
                'limitation':'Expected phrases and source URL checks; answers still need factual review. Timings include local machine load.'},ensure_ascii=False,indent=2),encoding='utf-8')
            print(f"{index}/60 {'PASS' if passed else 'FAIL'} {case['question']}",flush=True)
    report=json.loads(report_path.read_text(encoding='utf-8')) if report_path.exists() else {}
    report.update({'cases':60,'passed':sum(x['passed'] for x in results),'completed':len(results),
                   'results':results,'targeted_retry':retry_failed})
    report_path.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    if not all(x['passed'] for x in results):raise SystemExit(1)


if __name__=='__main__':
    sys.stdout.reconfigure(encoding='utf-8');main()
