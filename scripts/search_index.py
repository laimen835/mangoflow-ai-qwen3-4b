"""Build, calibrate and atomically swap active/standby vectors. No document deletion."""
import argparse
import asyncio
import json
import hashlib
from pathlib import Path
import httpx
from sqlalchemy import select,text
from src.core.config import settings
from src.core.db.models import Chunk,Document
from src.core.db.session import AsyncSessionLocal,engine
from src.core.rag.embeddings import build_embedder
from src.core.rag.profiles import state_for,embed_documents,fingerprint


async def corpus_stamp(db):
    rows=(await db.execute(select(Chunk.id,Chunk.content,Document.source_url,Document.status,Document.meta)
        .join(Document,Document.id==Chunk.document_id).order_by(Chunk.id))).all()
    return hashlib.sha256(json.dumps([list(r) for r in rows],default=str,ensure_ascii=False,sort_keys=True).encode()).hexdigest()


def approval_matches(report,profile,corpus):
    return (report.get('activation_approved') is True and report.get('retrieval_gate_passed') is True
        and report.get('reviewed_candidate_profile')==profile and report.get('reviewed_corpus_stamp')==corpus)


async def exchange_vectors(db,state):
    """One transaction exchanges vectors and their interpretation, including rollback."""
    missing=await db.scalar(select(Chunk.id).where(Chunk.embedding.is_not(None),Chunk.embedding_candidate.is_(None)).limit(1))
    if missing:raise RuntimeError('Standby index is incomplete')
    await db.execute(text('UPDATE chunks SET embedding=embedding_candidate, embedding_candidate=embedding'))
    state.active_profile,state.candidate_profile=state.candidate_profile,state.active_profile


async def build():
    async with AsyncSessionLocal() as db:
        state=await state_for(db,settings,for_update=True)
        if state.candidate_profile:
            raise RuntimeError('Standby profile already exists; preserve it or explicitly discard/rebuild')
        async with httpx.AsyncClient(trust_env=False) as client:
            response=await client.get(settings.ollama_base_url+'/api/tags');response.raise_for_status()
            digest=next((m['digest'] for m in response.json()['models'] if m['name'].split(':')[0]==settings.embedding_model.split(':')[0]),None)
        profile={'name':'nomic-search-v2','model':settings.embedding_model,'model_digest':digest,
            'dimension':settings.embedding_dim,'query_prefix':'search_query: ','document_prefix':'search_document: ',
            'normalize':True,'version':2,'policy':'semantic','rerank_threshold':-99.0,'fallback_vector_threshold':0.8}
        rows=(await db.scalars(select(Chunk).order_by(Chunk.id))).all()
        embedder=build_embedder(settings)
        for i in range(0,len(rows),16):
            batch=rows[i:i+16];vectors=await embed_documents(embedder,[c.content for c in batch],profile)
            for chunk,vector in zip(batch,vectors):chunk.embedding_candidate=vector
        docs=(await db.scalars(select(Document))).all()
        for doc in docs:
            doc.meta={**(doc.meta or {}),'_embedding_profiles':{
                **(doc.meta or {}).get('_embedding_profiles',{}),
                fingerprint(state.active_profile):doc.content_hash,fingerprint(profile):doc.content_hash}}
        state.candidate_profile=profile
        await db.commit()
        print('Standby built:',len(rows),'vectors. Active index unchanged.',flush=True)


async def swap():
    # Call only during the documented short API maintenance stop.
    async with AsyncSessionLocal() as db:
        state=await state_for(db,settings,for_update=True)
        if not state.candidate_profile:raise RuntimeError('No standby index')
        if state.candidate_profile.get('policy')=='semantic':
            report_path=Path('logs/search-comparison.json')
            report=json.loads(report_path.read_text(encoding='utf-8')) if report_path.exists() else {}
            if not approval_matches(report,state.candidate_profile,await corpus_stamp(db)):
                raise RuntimeError('Candidate has not passed quality, latency, factual review and regression gates')
        await exchange_vectors(db,state)
        await db.commit()
        print('Activated:',state.active_profile['name'],'Rollback standby:',state.candidate_profile['name'])


async def main():
    parser=argparse.ArgumentParser();parser.add_argument('action',choices=['build','swap','status']);args=parser.parse_args()
    if args.action=='build':await build()
    elif args.action=='swap':await swap()
    else:
        async with AsyncSessionLocal() as db:
            state=await state_for(db,settings)
            print(json.dumps({'active':state.active_profile,'standby':state.candidate_profile},ensure_ascii=False,indent=2))
    await engine.dispose()


if __name__=='__main__':
    try:asyncio.run(main())
    except RuntimeError as exc:raise SystemExit(str(exc)) from None
