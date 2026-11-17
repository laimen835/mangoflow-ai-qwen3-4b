"""Assemble gates from preserved measurements and a separate factual review."""
import json
from pathlib import Path
from xml.etree import ElementTree
from scripts.scholarship_pilot import ROOT,input_signature,check_approval


def read(path):return json.loads(Path(path).read_text(encoding='utf-8'))


def main():
    cases=read('tests/eval/scholarship_cases.json')['cases']
    manual=read(ROOT/'manual-review.json')
    baseline=read(ROOT/'baseline-all.json')['results']
    after=read(ROOT/'structured-holdout.json')['results']
    positive={c['id'] for c in cases if c['split']=='holdout' and c['answerable']}
    negative={c['id'] for c in cases if c['split']=='holdout' and not c['answerable']}
    reviewed=manual['structured'];lookup={r['id']:r for r in after}
    assert set(lookup)==positive|negative and set(reviewed)>={str(i) for i in lookup}
    correct=sum(reviewed[str(i)]['correct'] and lookup[i]['auto_passed'] and lookup[i]['pipeline']['evidence_in_context'] for i in positive)
    before=sum(r['auto_passed'] for r in baseline if r['id'] in positive)
    before_quality=read(ROOT/'quality-before.json');after_quality=read(ROOT/'quality-after.json')
    assert before_quality['completed']==after_quality['completed']==60
    old_failed={r['case'] for r in before_quality['results'] if not r['passed']}
    new_failed={r['case'] for r in after_quality['results'] if not r['passed']}
    tests=ElementTree.parse(ROOT/'tests.xml').getroot()
    suites=list(tests.iter('testsuite'));tests_ok=bool(suites) and all(int(s.get('failures','0'))+int(s.get('errors','0'))==0 for s in suites)
    guardrails=read(ROOT/'guardrail-results.json')
    gates={'correct_control_answers':int(correct),'improved_control_answers':int(correct-before),
           'unsupported_control_answers':sum(reviewed[str(i)]['unsupported_facts'] for i in negative),
           'p95_search_ms':read(ROOT/'search-timings.json')['structured']['p95_ms'],
           'regressions_passed':not (new_failed-old_failed),'tests_passed':tests_ok,
           'guardrails_passed':guardrails['passed']==guardrails['cases']==3}
    approved=correct>=3 and correct>before and gates['unsupported_control_answers']==0 and gates['p95_search_ms']<=3000 and all(gates[k] for k in ['regressions_passed','tests_passed','guardrails_passed'])
    report={'variant':'structured','selected_by':'Tuning: 3/4 complete answers versus 2/4 with recursive chunks; page and section provenance retained.',
        'promotion_approved':approved,'input_signature':input_signature(),'gates':gates,
        'known_regression_failures':sorted(new_failed&old_failed),'new_regression_failures':sorted(new_failed-old_failed),
        'tests':sum(int(s.get('tests','0')) for s in suites),'manual_review':manual,
        'limitations':['12-question pilot only; no conclusion about all topics.',
                      'Both added-data variants answered 4/4 held-out facts; the extra benefit of structural splitting is not proven on this small control.',
                      'Tuning question 2 remains partial: clause 4.3 is not among the selected fragments.',
                      'Replies may repeat validity warnings; exact current payment amounts remain unconfirmed.']}
    (ROOT/'acceptance.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    if approved:check_approval(report,'structured',report['input_signature'])
    print(json.dumps({'promotion_approved':approved,'gates':gates},ensure_ascii=False,indent=2))


if __name__=='__main__':main()
