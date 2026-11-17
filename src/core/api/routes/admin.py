from __future__ import annotations

from fastapi import APIRouter, Depends, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.api.deps import verify_admin, get_embedder
from src.core.api.schemas import IngestResponse, IngestUrlRequest
from src.core.config import settings
from src.core.db.session import get_db
from src.core.rag.ingest import ingest_url
from pydantic import BaseModel,Field
from typing import Literal
from src.core.rag.retriever import HybridRetriever
from src.core.rag.query import contextual_query
from sqlalchemy import select, or_
from src.core.db.models import Document
from src.core.rag.document_policy import exclusion_reasons


class SearchDebugRequest(BaseModel):
    query:str=Field(min_length=1,max_length=4000)
    index_slot:Literal['active','candidate']='active'
    history:list[dict]=Field(default_factory=list,max_length=10)

router = APIRouter(
    prefix="/admin",
    tags=["admin"],
    dependencies=[Depends(verify_admin)],
)


@router.post("/ingest/url", response_model=IngestResponse)
async def ingest_single_url(
    payload: IngestUrlRequest,
    db: AsyncSession = Depends(get_db),
    embedder=Depends(get_embedder),
) -> IngestResponse:
    return IngestResponse(**await ingest_url(
        db, url=payload.url, source_type=payload.source_type,
        meta=payload.meta, embedder=embedder, settings=settings,
    ))


@router.post("/sync", status_code=202)
async def update_sources(background_tasks: BackgroundTasks):
    from src.core.services.updates import run_update
    background_tasks.add_task(run_update)
    return {"accepted": True}


@router.post('/search/debug')
async def search_debug(payload:SearchDebugRequest,db:AsyncSession=Depends(get_db),embedder=Depends(get_embedder)):
    retriever=HybridRetriever(db,embedder,settings,index_slot=payload.index_slot)
    query=contextual_query(payload.query,payload.history)
    result=await retriever.retrieve(query)
    from src.core.rag.section_context import restore_reviewed_sections
    from src.core.rag.source_context import prepare_source_hits
    from src.core.rag.prompt import context_hits
    final_context=context_hits(restore_reviewed_sections(prepare_source_hits(result.hits, query), query))
    topic_docs=(await db.scalars(select(Document).where(or_(Document.meta.contains({'scholarship_material':True}),
                                                           Document.meta.contains({'review_required':True}))))).all()
    return {'query':query,'status':result.status,'diagnostics':result.diagnostics,
            'final_context':[{'chunk_id':h.chunk_id,'document_id':h.document_id,'content':h.content,
                              'section':h.meta.get('section'),'pages':h.meta.get('pages'),
                              'supporting_chunk_ids':h.meta.get('supporting_chunk_ids',[h.chunk_id]),
                              'section_restored':h.meta.get('section_context_restored',False)} for h in final_context],
            'quarantined_documents':[{'id':d.id,'url':d.source_url,'reasons':exclusion_reasons(d),'review':d.meta.get('review',{})} for d in topic_docs if exclusion_reasons(d)],
            'hits':[{'chunk_id':h.chunk_id,'title':h.title,'url':h.url,'content':h.content,
                     'hybrid_score':h.raw_score,'rerank_score':h.rerank_score,'provenance':h.meta}for h in result.hits]}
