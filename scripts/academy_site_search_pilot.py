"""Compare frozen local corpus retrieval; do not mutate documents or index profiles."""
import asyncio
import hashlib
import json
import math
import time
from pathlib import Path

from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine,async_sessionmaker
from src.core.config import settings
from src.core.rag.embeddings import build_embedder
from src.core.rag.query import contextual_query
from src.core.rag.retriever import HybridRetriever
from src.core.services.academy_scope import resolve_units


async def main():
    record=json.loads(Path('D:/Codex-RAG-runtime/academy-site-current.json').read_text())
    folder=Path(record['folder'])
    cases_file=Path('tests/eval/academy_site_pilot.json')
    dataset=json.loads(cases_file.read_text(encoding='utf-8'))
    report={'dataset_sha256':hashlib.sha256(cases_file.read_bytes()).hexdigest(),
            'corpus_sha256':dataset['corpus_sha256'],'limitation':dataset['limitation'],'modes':{}}
    for mode,dbname,enabled in (('baseline',record['baseline_database'],False),('hybrid_bge',record['candidate_database'],True)):
        assert dbname.startswith('rag_academy_')
        url=make_url(settings.database_url).set(database=dbname).render_as_string(hide_password=False)
        cfg=settings.model_copy(update={'database_url':url,'academy_rerank_enabled':enabled,
            'academy_reranker_directory':'D:/Codex-RAG-runtime/models/reranker-bge-v2-m3-int8'})
        engine=create_async_engine(url);session=async_sessionmaker(engine);embedder=build_embedder(cfg)
        rows=[];report['modes'][mode]={'rows':rows}
        try:
            async with session() as db:
                retriever=HybridRetriever(db,embedder,cfg)
                await retriever.retrieve('Прогрев поиска по академии')
                for case in dataset['cases']:
                    query=contextual_query(case['question'],case.get('history',[]))
                    retriever.unit_keys=tuple(u.key for u in resolve_units(case['question'],case.get('history',[])))
                    start=time.perf_counter();result=await retriever.retrieve(query,top_k=4)
                    expected={e['chunk_id'] for e in case['evidence']}
                    returned={h.chunk_id for h in result.hits}
                    row={'id':case['id'],'split':case['split'],'question':case['question'],'answerable':case['answerable'],
                         'expected_ids':sorted(expected),'found':bool(expected&returned),
                         'seconds':round(time.perf_counter()-start,3),'status':result.status,
                         'hits':[{'id':h.chunk_id,'url':h.url,'content':h.content,'score':h.rerank_score} for h in result.hits],
                         'diagnostics':result.diagnostics}
                    rows.append(row)
                    (folder/'search-comparison.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
                    print(mode,case['id'],'found',row['found'],'seconds',row['seconds'],flush=True)
            report['modes'][mode]['summary']={}
            for split in ('development','control'):
                positive=[r for r in rows if r['split']==split and r['answerable']]
                times=sorted(r['seconds'] for r in rows if r['split']==split)
                report['modes'][mode]['summary'][split]={'found':sum(r['found'] for r in positive),'answerable':len(positive),
                    'recall_at_4':sum(r['found'] for r in positive)/max(1,len(positive)),
                    'p95_s':times[math.ceil(.95*len(times))-1],
                    'negative_note':'Returned fragments are not a factual answer; unknown-question behavior requires separate chat checks.'}
            (folder/'search-comparison.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        finally:
            await engine.dispose()
    print(json.dumps({m:r['summary'] for m,r in report['modes'].items()},indent=2),flush=True)


if __name__=='__main__':asyncio.run(main())
