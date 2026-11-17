import pytest
from sqlalchemy import select
from src.core.db.models import Document,Chunk,DocumentStatus
from src.core.rag.document_policy import real_document_filter
from src.core.rag.ingest import ingest_text
from src.core.rag.chunker import TextChunk


@pytest.mark.asyncio
async def test_unverified_scholarship_documents_are_quarantined(db):
    ids=[]
    for review in [{'scope':'unknown','text_verified':True},{'scope':'spo_confirmed','text_verified':False},{'scope':'spo_confirmed','text_verified':True}]:
        doc=Document(title='Контроль политики',source_type='ncsa_scholarship',status=DocumentStatus.INDEXED,
                     meta={'scholarship_material':True,'review':review});db.add(doc);await db.flush();ids.append(doc.id)
    found=(await db.scalars(select(Document.id).where(Document.id.in_(ids),real_document_filter()))).all()
    assert found==[ids[-1]]


@pytest.mark.asyncio
async def test_same_text_reindexes_both_profiles_after_extraction_version_change(db,settings):
    class Embedder:
        calls=[]
        async def embed_many(self,texts):self.calls.append(texts);return [[0.1]*settings.embedding_dim for _ in texts]
    e=Embedder();args=dict(db=db,title='Проверка версий',text='Одинаковый текст',source_type='test',
        external_id='scholarship-version-test',embedder=e,settings=settings,
        prepared_chunks=[TextChunk('Одинаковый текст',0,{'page':4,'token_count':7})])
    first=await ingest_text(**args,processing_version='extraction-v1:chunks-v1')
    e.calls=[];assert (await ingest_text(**args,processing_version='extraction-v1:chunks-v1'))['status']=='skipped'
    assert not e.calls
    assert (await ingest_text(**args,processing_version='extraction-v2:chunks-v1'))['status']=='updated'
    chunk=(await db.scalars(select(Chunk).where(Chunk.document_id==first['document_id']))).one()
    assert chunk.embedding is not None and chunk.embedding_candidate is not None
    assert chunk.meta['page']==4 and chunk.token_count==7 and len(e.calls)==2
