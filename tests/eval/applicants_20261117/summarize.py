import csv
import hashlib
import json
import math
import statistics
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def timing(values):
    values = sorted(x for x in values if x is not None)
    return {'n':len(values), 'median_ms':round(statistics.median(values),2) if values else None,
            'p95_ms':round(values[math.ceil(.95*len(values))-1],2) if values else None}


def main():
    cases = json.loads((ROOT/'cases-frozen.json').read_text(encoding='utf-8'))['cases']
    judgments_path = ROOT/'judgments-blinded.json'
    judgments = json.loads(judgments_path.read_text(encoding='utf-8'))
    key = json.loads((ROOT/'review-key.json').read_text(encoding='utf-8'))
    assert set(judgments) == {c['id'] for c in cases}
    grade = {}
    for case in cases:
        for side in ('A','B'):
            item = judgments[case['id']][side]
            assert item['score'] in (0,1,2) and item['note']
            grade[case['id'], key[case['id']][side]] = item
    reports = {m:json.loads((ROOT/f'answers-{m}.json').read_text(encoding='utf-8')) for m in ('4b','9b')}
    summary = {'models':{}, 'paired':{'4b_better':0,'9b_better':0,'ties':0},
        'judgment_sha256':hashlib.sha256(judgments_path.read_bytes()).hexdigest(),
        'case_sha256':reports['4b']['case_sha256']}
    public_rows = []
    for label, report in reports.items():
        rows = report['rows']
        scores = [grade[c['id'],label] for c in cases]
        positive = [(c, grade[c['id'],label]) for c in cases if c['expected_kind']=='answer']
        gaps = [(c, grade[c['id'],label]) for c in cases if c['expected_kind']=='gap']
        ambiguous = [(c, grade[c['id'],label]) for c in cases if c['expected_kind']=='clarify']
        calls = [call for row in rows for call in row['calls']]
        summary['models'][label] = {
            'model':report['model'], 'questions':len(rows),
            'completed':sum(bool(r.get('response')) for r in rows),
            'model_called_questions':sum(bool(r['calls']) for r in rows),
            'model_calls':len(calls),
            'answered_full':sum(s['class']=='full' for c,s in positive),
            'answerable_questions':len(positive),
            'partial_answerable':sum(s['class']=='partial_safe' for c,s in positive),
            'false_refusals':sum(s['class']=='false_refusal' for c,s in positive),
            'honest_gaps':sum(s['class'] in ('honest_gap','clarify') for c,s in gaps),
            'gap_questions':len(gaps),
            'necessary_clarifications':sum(s['class']=='clarify' for c,s in ambiguous),
            'ambiguous_questions':len(ambiguous),
            'misleading_or_wrong':sum(s['class'] in ('wrong_scope','wrong_fact','nonresponsive') for s in scores),
            'technical_failures':sum(s['class']=='technical_failure' for s in scores),
            'total_utility_points':sum(s['score'] for s in scores),
            'classes':{kind:sum(s['class']==kind for s in scores) for kind in sorted({s['class'] for s in scores})},
            'full_answer_timing':timing([r['total_ms'] for r in rows]),
            'first_text_timing':timing([r['first_text_ms'] if r['first_text_ms'] is not None else r['total_ms']
                                      for r in rows if r.get('response')]),
            'search_timing':timing([(r.get('response') or {}).get('usage',{}).get('search_ms') for r in rows]),
            'llm_call_timing':timing([call['wall_ms'] for call in calls]),
            'full_answer_when_model_called':timing([r['total_ms'] for r in rows if r['calls']]),
            'excluded_warmup_ms':report['excluded_warmup_ms'],
        }
        for c,row in zip(cases,rows):
            s=grade[c['id'],label]
            public_rows.append({'id':c['id'],'question':c['question'],'category':c['category'],
                'expected_kind':c['expected_kind'],'model':report['model'],'class':s['class'],
                'score':s['score'],'assessment':s['note'],
                'answer':(row.get('response') or {}).get('answer',''),
                'full_ms':row['total_ms'],'first_delta_ms':row['first_text_ms'],
                'llm_called':bool(row['calls']),
                'sources':json.dumps((row.get('response') or {}).get('sources',[]),ensure_ascii=False)})
    for case in cases:
        left,right=grade[case['id'],'4b']['score'],grade[case['id'],'9b']['score']
        summary['paired']['4b_better' if left>right else '9b_better' if right>left else 'ties']+=1
    diagnostics=json.loads((ROOT/'paired-diagnostics.json').read_text(encoding='utf-8'))
    summary['same_model_inputs']=sum(r['same_model_inputs'] for r in diagnostics)
    summary['identical_answers']=sum(r['same_answer'] for r in diagnostics)
    (ROOT/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    with (ROOT/'all-answers.csv').open('w',encoding='utf-8-sig',newline='') as output:
        writer=csv.DictWriter(output,fieldnames=list(public_rows[0]));writer.writeheader();writer.writerows(public_rows)
    print(json.dumps(summary,ensure_ascii=False,indent=2))


if __name__=='__main__':
    main()
