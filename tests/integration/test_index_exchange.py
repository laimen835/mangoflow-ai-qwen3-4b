import pytest
from sqlalchemy import select
from src.core.db.models import Chunk
from src.core.rag.profiles import state_for
from scripts.search_index import exchange_vectors,approval_matches


@pytest.mark.asyncio
async def test_atomic_exchange_and_reverse_restore_profiles_and_vectors(db,settings):
    state=await state_for(db,settings,for_update=True)
    before=(dict(state.active_profile),dict(state.candidate_profile))
    stmt=select(Chunk.embedding,Chunk.embedding_candidate).order_by(Chunk.id).limit(1)
    a,b=(await db.execute(stmt)).one()
    await exchange_vectors(db,state)
    swapped_a,swapped_b=(await db.execute(stmt)).one()
    assert list(swapped_a)==list(b) and list(swapped_b)==list(a)
    assert state.active_profile==before[1] and state.candidate_profile==before[0]
    await exchange_vectors(db,state)
    restored_a,restored_b=(await db.execute(stmt)).one()
    assert list(restored_a)==list(a) and list(restored_b)==list(b)
    assert (state.active_profile,state.candidate_profile)==before
    # Fixture rolls the enclosing transaction back; production profile never changes.


def test_activation_requires_passed_gates_and_unchanged_profile_and_corpus():
    profile={'name':'candidate','rerank_threshold':-4}
    r={'activation_approved':True,'retrieval_gate_passed':True,
       'reviewed_candidate_profile':profile,'reviewed_corpus_stamp':'corpus'}
    assert approval_matches(r,profile,'corpus')
    assert not approval_matches({**r,'retrieval_gate_passed':False},profile,'corpus')
    assert not approval_matches(r,{**profile,'rerank_threshold':-8},'corpus')
    assert not approval_matches(r,profile,'changed')
