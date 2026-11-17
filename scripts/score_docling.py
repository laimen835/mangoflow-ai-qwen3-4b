"""Compare exact, source-checked anchors; an extraction success flag is not QA."""
import json,re
from pathlib import Path
ROOT=Path('D:/Codex-RAG-runtime/scholarship-pilot')
ANCHORS={
 '24d1af547494f15b.pdf':['студенты СПК СКГА','принимается ректором на основании личного заявления',
    'вне зависимости от получения академической, социальной','за счет средств федерального бюджета',
    'дорогостоящего лечения','при создании семьи в течение 12 месяцев с даты регистрации брака',
    'несовершеннолетний ребенок (не более 2 раз в год)','трехкратный размер прожиточного минимума'],
 'f748cce1ee563259.pdf':['программам среднего профессионального образования','полное государственное обеспечение приостанавливается с даты предоставления академического отпуска'],
 '554478bc7c3d6082.pdf':['2023-2024','23.02.07-231','21.02.05-221','Гурина Ирина Алексеевна']}


def norm(s):return re.sub(r'\s+',' ',s).casefold().replace('ё','е').replace(' - ',' — ')


def main():
    results=[]
    for filename,anchors in ANCHORS.items():
        old=json.loads((ROOT/'snapshot-v2'/(filename+'.extracted.json')).read_text(encoding='utf-8'))
        new=json.loads((ROOT/'docling'/(filename+'.json')).read_text(encoding='utf-8'))
        before=norm(' '.join(b['text'] for b in old['blocks']));after=norm(new.get('text',''))
        checks=[{'anchor':a,'before':norm(a) in before,'docling':norm(a) in after} for a in anchors]
        results.append({'filename':filename,'checks':checks,'before_method':old['method'],'docling_seconds':new.get('seconds'),'error':new.get('error')})
    allchecks=[c for r in results for c in r['checks']]
    approved=all(c['docling'] for c in allchecks) and any(c['docling'] and not c['before'] for c in allchecks)
    report={'approved':approved,'selected_pdf_extractor':'docling' if approved else 'existing_pymupdf_and_reviewed_windows_ocr',
        'reason':'All anchors must survive without numeric damage and at least one extraction defect must improve.',
        'results':results,'limitations':'Exact anchor checks supplemented by manual inspection; not a general parser benchmark.'}
    (ROOT/'docling-comparison.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print('Docling approved:',approved,'anchors',sum(c['docling'] for c in allchecks),'/',len(allchecks))


if __name__=='__main__':main()
