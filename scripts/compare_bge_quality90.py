"""Offline multilingual embedding experiment. Does not change database vectors."""
import asyncio,json,hashlib,time,math
from pathlib import Path
import httpx,numpy as np
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine,async_sessionmaker
from src.core.config import settings
from src.core.rag.evidence_search import catalog,word_terms,score_catalog
from src.core.rag.query import contextual_query
from scripts.quality90_workspace import ROOT


async def main():
    url=make_url(settings.database_url).set(database='rag_quality90_candidate').render_as_string(hide_password=False)
    engine=create_async_engine(url);sessions=async_sessionmaker(engine)
    try:
        async with sessions() as db:data=await catalog(db)
    finally:await engine.dispose()
    cases=json.loads(Path('tests/eval/search_cases.json').read_text(encoding='utf-8'))['cases']
    documents=[reflow(e.hit.title+'\n'+e.hit.content) for e in data.entries]
    corpus_hash=hashlib.sha256(json.dumps([(e.hit.chunk_id,t) for e,t in zip(data.entries,documents)],ensure_ascii=False).encode()).hexdigest()
    ids=np.array([e.hit.chunk_id for e in data.entries],dtype=np.int64)
    async with httpx.AsyncClient(timeout=300,trust_env=False) as client:
        tags=(await client.get(settings.ollama_base_url+'/api/tags')).json()['models']
        digest=next(m['digest'] for m in tags if m['name'].split(':')[0]=='bge-m3')
        async def embed(texts):
            r=await client.post(settings.ollama_base_url+'/api/embed',json={'model':'bge-m3','input':texts,'keep_alive':'30m'})
            r.raise_for_status();d=r.json()
            if 'embeddings' not in d:raise RuntimeError(d.get('error','No embeddings'))
            matrix=np.asarray(d['embeddings'],dtype=np.float32)
            if len(matrix)!=len(texts) or matrix.ndim!=2 or matrix.shape[1]!=1024:raise ValueError('Unexpected BGE dimensions')
            return matrix/np.maximum(np.linalg.norm(matrix,axis=1,keepdims=True),1e-9)
        target=ROOT/'bge-m3-corpus.npz'
        if target.exists():
            saved=np.load(target,allow_pickle=False)
            if str(saved['corpus_hash'])!=corpus_hash or str(saved['model_digest'])!=digest:raise ValueError('Cached BGE index profile changed')
            vectors=saved['vectors']
        else:
            parts=[]
            for start in range(0,len(documents),8):
                parts.append(await embed(documents[start:start+8]));print('BGE indexed',min(start+8,len(documents)),'/',len(documents),flush=True)
            vectors=np.concatenate(parts)
            np.savez_compressed(target,vectors=vectors,ids=ids,corpus_hash=corpus_hash,model_digest=digest)
        await embed(['Прогрев поиска колледжа'])
        results=[]
        for case in cases:
            q=contextual_query(case['question'],case.get('history',[]));start=time.perf_counter()
            query=(await embed([q]))[0];scores=vectors@query
            order=np.argsort(-scores)[:4]
            expected={e['chunk_id'] for e in case['evidence']}
            hits=[{'chunk_id':int(ids[i]),'score':float(scores[i]),'url':data.entries[i].hit.url,'content':data.entries[i].hit.content} for i in order]
            results.append({'id':case['id'],'question':case['question'],'answerable':case['answerable'],
                'found':bool(expected&{h['chunk_id'] for h in hits}),'hits':hits,'search_ms':round((time.perf_counter()-start)*1000,2)})
        times=sorted(r['search_ms'] for r in results)
        report={'experiment':'BGE-M3 dense-only, development set previously inspected; no production promotion.',
            'model_digest':digest,'corpus_hash':corpus_hash,'dimension':1024,
            'evidence_found':sum(r['found'] for r in results),'answerable':60,
            'p95_ms':times[math.ceil(.95*len(times))-1],'results':results}
        (ROOT/'bge-development.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        print('BGE evidence',report['evidence_found'],'/60; p95',report['p95_ms'],flush=True)


def reflow(text):
    import re
    return re.sub(r'\s+',' ',text).strip()


if __name__=='__main__':asyncio.run(main())
