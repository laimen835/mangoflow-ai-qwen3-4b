"""Private, exact-text embedding checkpoints for resumable ingestion only."""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import sqlite3
import zlib

from src.core.rag.embeddings import EmbeddingProvider


class CheckpointEmbeddings(EmbeddingProvider):
    def __init__(self, provider, path, identity):
        self.provider=provider
        self.dim=provider.dim
        self.name=provider.name
        self.namespace=hashlib.sha256(json.dumps(identity,sort_keys=True).encode()).hexdigest()
        self.db=sqlite3.connect(Path(path))
        self.db.execute('CREATE TABLE IF NOT EXISTS vectors (namespace TEXT, text_hash TEXT, vector BLOB, PRIMARY KEY(namespace,text_hash))')
        self.db.commit()
        self.hits=self.created=0

    def key(self,text):
        return hashlib.sha256(text.encode()).hexdigest()

    def validate(self,vector):
        if len(vector)!=self.dim or any(not isinstance(v,(float,int)) or not math.isfinite(v) for v in vector):
            raise ValueError('Invalid cached embedding shape or values')
        return vector

    async def embed_one(self,text):
        return (await self.embed_many([text]))[0]

    async def embed_many(self,texts):
        found={}
        for text in texts:
            key=self.key(text)
            row=self.db.execute('SELECT vector FROM vectors WHERE namespace=? AND text_hash=?',(self.namespace,key)).fetchone()
            if row:
                found[key]=self.validate(json.loads(zlib.decompress(row[0])))
                self.hits+=1
        pending=list(dict.fromkeys(t for t in texts if self.key(t) not in found))
        for start in range(0,len(pending),32):
            batch=pending[start:start+32]
            vectors=await self.provider.embed_many(batch)
            if len(vectors)!=len(batch):
                raise ValueError('Embedding checkpoint batch count mismatch')
            values=[self.validate(v) for v in vectors]
            for text,vector in zip(batch,values):
                key=self.key(text)
                found[key]=vector
                compressed=zlib.compress(json.dumps(vector,separators=(',',':')).encode())
                self.db.execute('INSERT OR REPLACE INTO vectors VALUES (?,?,?)',(self.namespace,key,compressed))
            self.db.commit()
            self.created+=len(batch)
            if self.created%1024<len(batch):
                print(json.dumps({'embedding_checkpoint_created':self.created,'embedding_checkpoint_hits':self.hits}),flush=True)
        return [found[self.key(text)] for text in texts]

    def close(self):
        self.db.close()


async def checkpoint_identity(db,settings):
    from src.core.rag.profiles import state_for,fingerprint
    state=await state_for(db,settings)
    profiles=[fingerprint(p) for p in (state.active_profile,state.candidate_profile) if p]
    # Resolve the local model tag to its actual digest. Never reuse vectors after
    # replacing model weights under an unchanged tag, even on legacy profiles.
    import httpx
    async with httpx.AsyncClient(timeout=15,trust_env=False) as client:
        response=await client.get(settings.ollama_base_url.rstrip('/')+'/api/tags')
        response.raise_for_status()
        models=response.json().get('models',[])
    canonical=settings.embedding_model if ':' in settings.embedding_model else settings.embedding_model+':latest'
    model=next((m for m in models if m.get('name') in {settings.embedding_model,canonical}),None)
    if not model or not model.get('digest'):
        raise ValueError('Cannot bind embedding checkpoints to a local model digest')
    for profile in (state.active_profile,state.candidate_profile):
        expected=(profile or {}).get('model_digest')
        if expected and expected.removeprefix('sha256:')!=model['digest'].removeprefix('sha256:'):
            raise ValueError('Local embedding digest differs from the index profile')
    return {'provider':settings.embedding_provider,'model':settings.embedding_model,'digest':model['digest'],
            'dimension':settings.embedding_dim,'profiles':profiles,'cache_version':'exact-prepared-text-v1'}
