"""Create a compact review with model names hidden until assessments are frozen."""
import hashlib
import json
import random
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def norm(text):
    return re.sub(r'\s+', ' ', text).strip().casefold().replace('ё','е')


def main():
    a = json.loads((ROOT/'answers-4b.json').read_text(encoding='utf-8'))
    b = json.loads((ROOT/'answers-9b.json').read_text(encoding='utf-8'))
    assert a['status'] == b['status'] == 'complete'
    cases_path = ROOT/'cases-frozen.json'
    assert a['case_sha256'] == b['case_sha256'] == hashlib.sha256(cases_path.read_bytes()).hexdigest()
    assert a['corpus_before'] == b['corpus_before'] == a['corpus_after'] == b['corpus_after']
    assert a['code_before'] == b['code_before'] == a['code_after'] == b['code_after']
    cases = json.loads(cases_path.read_text(encoding='utf-8'))['cases']
    assert [r['id'] for r in a['rows']] == [r['id'] for r in b['rows']] == [r['id'] for r in cases]
    randomizer = random.Random(2026111701)
    keys, review, diagnostics = {}, [], []
    for case, ra, rb in zip(cases, a['rows'], b['rows']):
        pair = [('4b',ra), ('9b',rb)]
        randomizer.shuffle(pair)
        keys[case['id']] = {side: label for side, (label, _) in zip(('A','B'), pair)}
        item = {k: case[k] for k in ('id','question','category','expected_kind','rubric','history')}
        item['reference'] = [{'id':r['id'],'url':r['url'],'content':r['content']} for r in case['references']]
        for side, (_, row) in zip(('A','B'), pair):
            response = row.get('response') or {}
            usage = response.get('usage',{})
            text = response.get('answer','')
            item[side] = {'answer': text, 'sources': response.get('sources',[]),
                'route': usage.get('route'), 'fallback_type': usage.get('fallback_type'),
                'selection_status': usage.get('selection_status'), 'error':row.get('error'),
                'all_literal_patterns': all(re.search(p, text, re.I) for p in case['required_patterns'])
                    if case['required_patterns'] else None}
        item['same_answer'] = norm(item['A']['answer']) == norm(item['B']['answer'])
        review.append(item)
        ha = [c['messages_sha256'] for c in ra['calls']]
        hb = [c['messages_sha256'] for c in rb['calls']]
        diagnostics.append({'id':case['id'], 'same_model_inputs': ha == hb,
            'calls_4b':len(ha), 'calls_9b':len(hb), 'same_answer':item['same_answer']})
    for name, value in [('review-blinded.json', review), ('review-key.json', keys),
                        ('paired-diagnostics.json', diagnostics)]:
        path = ROOT/name
        assert not path.exists()
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'questions':len(review),'identical_answers':sum(r['same_answer'] for r in review),
        'matching_model_inputs':sum(r['same_model_inputs'] for r in diagnostics),
        'calls_4b':sum(r['calls_4b'] for r in diagnostics),
        'calls_9b':sum(r['calls_9b'] for r in diagnostics)},ensure_ascii=False))


if __name__ == '__main__':
    main()
