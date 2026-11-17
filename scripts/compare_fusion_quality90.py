"""Development-only Nomic/BGE/BM25 fusion and reranking of a balanced pool."""
import asyncio,json,re,time,math
from collections import defaultdict
from pathlib import Path
import numpy as np,httpx
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine,async_sessionmaker
from src.core.config import settings
from src.core.rag.evidence_search import catalog,word_terms,score_catalog,search_expansion
from src.core.rag.embeddings import build_embedder
from src.core.rag.vector_search import vector_search
from src.core.rag.query import contextual_query
from src.core.rag.reranker import rerank
from scripts.quality90_workspace import ROOT


def rerank_text(hit):
    text=re.sub(r'https?://\S+',' ',hit.content)
    return hit.title[:160]+'\n'+re.sub(r'\s+',' ',text).strip()


async def main():
    url=make_url(settings.database_url).set(database='rag_quality90_candidate').render_as_string(hide_password=False)
    cfg=settings.model_copy(update={'database_url':url});engine=create_async_engine(url);sessions=async_sessionmaker(engine)
    cases=json.loads(Path('tests/eval/search_cases.json').read_text(encoding='utf-8'))['cases']
    cache=np.load(ROOT/'bge-m3-corpus.npz',allow_pickle=False);vectors=cache['vectors'];ids=cache['ids']
    embedder=build_embedder(cfg);results=[]
    try:
        async with sessions() as db,httpx.AsyncClient(timeout=300,trust_env=False) as client:
            data=await catalog(db);byid={e.hit.chunk_id:e.hit for e in data.entries}
            assert list(ids)==[e.hit.chunk_id for e in data.entries]
            await rerank('Прогрев',['Пример материала колледжа'],cfg.reranker_directory)
            for case in cases:
                query=contextual_query(case['question'],case.get('history',[]));start=time.perf_counter()
                q=await client.post(cfg.ollama_base_url+'/api/embed',json={'model':'bge-m3','input':query,'keep_alive':'30m'});q.raise_for_status()
                vector=np.asarray(q.json()['embeddings'][0],dtype=np.float32);vector/=max(np.linalg.norm(vector),1e-9)
                bge=[int(ids[i]) for i in np.argsort(-(vectors@vector))[:30]]
                nomic=[h.chunk_id for h in await vector_search(db,await embedder.embed_one(query),limit=30)]
                lexical=[e.hit.chunk_id for e,s,m in score_catalog(data,word_terms(search_expansion(query)))[:30] if m]
                scores=defaultdict(float)
                for rows in [bge,nomic,lexical]:
                    for rank,cid in enumerate(rows):scores[cid]+=1/(60+rank)
                balanced=[]
                for source in [bge,nomic,lexical]:
                    for cid in source[:6]:
                        if cid not in balanced:balanced.append(cid)
                for cid in sorted(scores,key=scores.get,reverse=True):
                    if len(balanced)>=30:break
                    if cid not in balanced:balanced.append(cid)
                candidates=[byid[cid] for cid in balanced]
                logits=await rerank(query,[rerank_text(h) for h in candidates],cfg.reranker_directory)
                ranked=sorted(zip(candidates,logits),key=lambda p:p[1],reverse=True)
                expected={e['chunk_id'] for e in case['evidence']}
                results.append({'id':case['id'],'question':case['question'],'answerable':case['answerable'],
                    'found':bool(expected&{h.chunk_id for h,s in ranked[:4]}),'pool_contains_evidence':bool(expected&set(balanced)),
                    'hits':[{'chunk_id':h.chunk_id,'url':h.url,'content':h.content,'logit':float(s)} for h,s in ranked[:4]],
                    'search_ms':round((time.perf_counter()-start)*1000,2)})
                if len(results)%10==0:print('Fusion checked',len(results),'/',len(cases),flush=True)
    finally:await engine.dispose()
    times=sorted(r['search_ms'] for r in results)
    report={'experiment':'Development fusion+reranker, no relevance cutoff selected and no production activation.',
        'found':sum(r['found'] for r in results),'answerable':60,'p95_ms':times[math.ceil(.95*len(times))-1],
        'limitation':'Previously inspected questions; retrieval only, not the independent 90% answer gate.','results':results}
    (ROOT/'fusion-development.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print('Fusion evidence',report['found'],'/60; p95',report['p95_ms'],flush=True)


if __name__=='__main__':asyncio.run(main())
