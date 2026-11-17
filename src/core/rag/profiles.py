"""Database-owned index profiles prevent mixed query/document vector formats."""
import hashlib
import json
import re
from sqlalchemy import Integer,select
from sqlalchemy.orm import Mapped,mapped_column
from sqlalchemy.dialects.postgresql import JSONB
from src.core.db.session import Base
from src.core.rag.exceptions import RetrievalError


class RetrievalIndexState(Base):
    __tablename__='retrieval_index_state'
    id:Mapped[int]=mapped_column(Integer,primary_key=True)
    active_profile:Mapped[dict]=mapped_column(JSONB,nullable=False)
    candidate_profile:Mapped[dict|None]=mapped_column(JSONB,nullable=True)


def fingerprint(profile):
    keys={k:profile.get(k) for k in ('model','model_digest','dimension','query_prefix','document_prefix','normalize','version')}
    return hashlib.sha256(json.dumps(keys,sort_keys=True).encode()).hexdigest()[:24]


def prepare(text,profile,kind):
    if profile.get('normalize'):text=re.sub(r'\s+',' ',text).strip()
    return profile.get(kind+'_prefix','')+text


async def state_for(db,settings,*,for_update=False):
    stmt=select(RetrievalIndexState).where(RetrievalIndexState.id==1).execution_options(populate_existing=True)
    if for_update:stmt=stmt.with_for_update()
    row=(await db.execute(stmt)).scalar_one_or_none()
    if row is None:raise RetrievalError('Search index profile missing; run migrations')
    for profile in (row.active_profile,row.candidate_profile):
        if profile and (profile['model']!=settings.embedding_model or profile['dimension']!=settings.embedding_dim):
            raise RetrievalError('Embedding model does not match the stored index profile')
    return row


async def embed_documents(embedder,texts,profile):
    vectors=await embedder.embed_many([prepare(t,profile,'document') for t in texts])
    if len(vectors)!=len(texts) or any(len(v)!=profile['dimension'] for v in vectors):
        raise RetrievalError('Embedding batch does not match the index profile')
    return vectors
