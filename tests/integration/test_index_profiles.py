import pytest
from sqlalchemy import select
from src.core.rag.ingest import ingest_text
from src.core.rag.profiles import state_for,fingerprint
from src.core.db.models import Chunk


@pytest.mark.asyncio
async def test_same_text_reindexes_after_profile_change_and_preserves_metadata(db,settings):
    class Embedder:
        def __init__(self):self.calls=[]
        async def embed_many(self,texts):
            self.calls.append(texts)
            return [[0.2]+[0.0]*(settings.embedding_dim-1) for _ in texts]
    e=Embedder()
    args=dict(db=db,title='ТЕСТ: профили индекса',text='Проверяемый текст документа.',source_type='test',
              external_id='profile-regression-v2',embedder=e,settings=settings)
    first=await ingest_text(**args,meta={'department':'Keep this'})
    e.calls.clear()
    assert (await ingest_text(**args))['status']=='skipped' and not e.calls
    state=await state_for(db,settings,for_update=True)
    state.candidate_profile={**state.active_profile,'name':'test-next','version':99,'document_prefix':'search_document: '}
    await db.flush()
    result=await ingest_text(**args)
    assert result['status']=='updated' and any(t.startswith('search_document: ') for call in e.calls for t in call)
    from src.core.db.models import Document
    doc=await db.get(Document,first['document_id'])
    assert doc.meta['department']=='Keep this'
    assert fingerprint(state.candidate_profile) in doc.meta['_embedding_profiles']
    row=(await db.scalars(select(Chunk).where(Chunk.document_id==doc.id))).first()
    assert row.embedding is not None and row.embedding_candidate is not None
    e.calls.clear()
    changed={**args,'settings':settings.model_copy(update={'chunk_size':160,'chunk_overlap':20})}
    assert (await ingest_text(**changed))['status']=='updated' and e.calls
    assert doc.meta['_chunking_profile']=='recursive-v2:160:20'
