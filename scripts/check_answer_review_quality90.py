"""Real local-model probes for previously observed errors; not holdout scoring."""
import asyncio,json
from pathlib import Path
from src.core.config import settings
from src.core.llm import build_llm
from src.core.rag.answer_review import review_answer
from src.core.rag.vector_search import SearchHit
from scripts.quality90_workspace import ROOT


async def main():
    docs=json.loads((ROOT/'initial-corpus.json').read_text(encoding='utf-8'))
    doc=next(d for d in docs if d['id']==205)
    def clause(number,needle=None):
        rows=[c for c in doc['chunks'] if c['meta'].get('section','').endswith(number)]
        if needle:rows=[c for c in rows if needle in c['text']]
        c=rows[0]
        return SearchHit(c['id'],doc['id'],c['text'],doc['title'],doc['url'],1,1,'probe',
            meta={**c['meta'],'document_review':doc['meta']['review'],'scholarship_material':True})
    family=clause('4.9','регистрации брака');decision=clause('4.3')
    cases=[
      ('family_false_absence','Какой срок указан для помощи после регистрации брака по положению 2025 года?',
       'Срок — 12 месяцев. Документ не уточняет, что поддержка касается молодых семей.',[family]),
      ('wrong_decision_maker','Кто принимает решение о помощи и её размере по пункту 4.3?',
       'Размер помощи определяет директор колледжа.',[decision]),
      ('invented_payment','Сколько рублей мне гарантируют по этому документу?',
       'Вам гарантированно выплатят 5000 рублей.',[family]),
      ('correct_short_answer','Какой срок после регистрации брака указан в положении?',
       'В течение 12 месяцев с даты регистрации брака.',[family]),
    ]
    results=[];llm=build_llm(settings)
    for name,question,draft,hits in cases:
        result=await review_answer(llm,question,draft,hits)
        row={'case':name,'question':question,'draft':draft,'answer':result.text,'status':result.status,
             'evidence':result.evidence,'rejected':result.rejected,'milliseconds':result.milliseconds,'raw':result.raw,
             'source_fragments':[h.content for h in hits]}
        results.append(row)
        (ROOT/'answer-review-probes.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')
        print(name,result.status,result.milliseconds,flush=True)


if __name__=='__main__':asyncio.run(main())
