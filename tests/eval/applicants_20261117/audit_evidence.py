"""Read-only admission and literal-copy audit for the synthetic model runs."""
import asyncio
import json
import re
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.engine import make_url
from src.core.config import settings
from src.core.db.models import Chunk, Document, DocumentStatus
from src.core.db.session import AsyncSessionLocal, engine
from src.core.rag.document_policy import real_document_filter

ROOT = Path(__file__).resolve().parent


def norm(value):
    return re.sub(r'\s+', ' ', value).strip()


async def main():
    url=make_url(settings.database_url)
    assert url.database=='rag_admissions_eval_20261117' and url.port==55433
    reports={label:json.loads((ROOT/f'answers-{label}.json').read_text(encoding='utf-8'))
             for label in ('4b','9b')}
    assert all(report['status']=='complete' for report in reports.values())
    chunk_ids={source['chunk_id'] for report in reports.values() for row in report['rows']
               for source in (row.get('response') or {}).get('sources',[])}
    async with AsyncSessionLocal() as db:
        allowed=set((await db.execute(select(Chunk.id).join(Document).where(
            Chunk.id.in_(chunk_ids), Document.status==DocumentStatus.INDEXED,
            real_document_filter()))).scalars())
    result={'unique_cited_chunks':len(chunk_ids),'all_citations_admitted':chunk_ids<=allowed,
            'unresolved_chunk_ids':sorted(chunk_ids-allowed),'models':{}}
    for label, report in reports.items():
        rows=[]
        for row in report['rows']:
            response=row.get('response') or {};usage=response.get('usage',{})
            selected=usage.get('selected_chunk_ids',[])
            if not selected:continue
            ids=[s['chunk_id'] for s in response.get('sources',[])]
            assert ids==selected
            materials=[]
            for call in row['calls']:
                if call['kind']!='structured':continue
                try:materials.append(json.loads(call['messages'][-1]['content'])['materials'])
                except (ValueError,KeyError):pass
            text=response['answer']
            assert text.startswith('В найденных источниках указано:')
            text=text.removeprefix('В найденных источниках указано:').strip()
            text=text.split('Это частичное подтверждение:')[0].strip()
            text=text.split('Ответ описывает опубликованные материалы.')[0].strip()
            quotes=[part.strip() for part in re.split(r'\s*\[\d+\]\s*',text) if part.strip()]
            literal=bool(materials) and all(any(norm(q) in norm(m) for m in materials) for q in quotes)
            rows.append({'id':row['id'],'literal_text_in_model_context':literal,
                         'selected_chunks_admitted':set(selected)<=allowed})
        result['models'][label]={'selected_source_answers':len(rows),
            'literal_copy_verified':sum(r['literal_text_in_model_context'] for r in rows),
            'rows':rows}
    path=ROOT/'evidence-audit.json'
    assert not path.exists()
    path.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({**result,'models':{k:{a:b for a,b in v.items() if a!='rows'} for k,v in result['models'].items()}},ensure_ascii=False))
    await engine.dispose()


if __name__=='__main__':
    asyncio.run(main())
