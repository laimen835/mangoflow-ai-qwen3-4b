from __future__ import annotations

import hashlib

import httpx
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import Settings
from src.core.db.models import Chunk, Document, DocumentStatus
from src.core.rag.chunker import RecursiveChunker
from src.core.rag.embeddings import EmbeddingProvider
from src.core.rag.exceptions import IngestionError
from src.core.rag.profiles import state_for, fingerprint, embed_documents

from bs4 import BeautifulSoup


async def ingest_text(
        db: AsyncSession,
        *,
        title: str,
        text: str,
        source_type: str = "web",
        source_url: str | None = None,
        external_id: str | None = None,
        meta: dict | None = None,
        embedder: EmbeddingProvider,
        settings: Settings,
        prepared_chunks=None,
        processing_version: str | None = None,
) -> dict:
    """
    Индексирует один документ. Возвращает {document_id, status, chunks_created}.
    """
    if not text.strip():
        raise IngestionError("Empty text")

    content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    state = await state_for(db,settings,for_update=True)
    profiles = [p for p in (state.active_profile,state.candidate_profile) if p]

    # 1. Upsert документа
    existing: Document | None = None
    if external_id:
        stmt = select(Document).where(
            Document.source_type == source_type,
            Document.external_id == external_id,
        )
        existing = (await db.execute(stmt)).scalar_one_or_none()

    recorded = (existing.meta or {}).get('_embedding_profiles',{}) if existing else {}
    chunking_profile = processing_version or f'recursive-v2:{settings.chunk_size}:{settings.chunk_overlap}'
    old_chunking = (existing.meta or {}).get('_chunking_profile','recursive-v2:800:120') if existing else None
    profiles_ready = all(recorded.get(fingerprint(p))==content_hash or
        (p['name']=='legacy-v1' and not recorded) for p in profiles)
    if existing and existing.content_hash == content_hash and existing.status == DocumentStatus.INDEXED and profiles_ready and old_chunking==chunking_profile:
        existing.title = title
        existing.source_url = source_url
        existing.meta = {**((existing.meta or {}) if meta is None else meta),
                         '_embedding_profiles':recorded,'_chunking_profile':chunking_profile}
        await db.commit()
        return {
            "document_id": existing.id,
            "status": "skipped",
            "chunks_created": 0,
        }

    # 2. Чанкинг
    chunker = RecursiveChunker(
        chunk_size=settings.chunk_size,
        chunk_overlap=settings.chunk_overlap,
    )
    text_chunks = prepared_chunks if prepared_chunks is not None else chunker.split(text, base_meta={"title": title})

    if not text_chunks:
        raise IngestionError("No chunks produced")

    # 3. Эмбеддинги (батчем — быстрее и дешевле)
    try:
        embeddings = await embed_documents(embedder,[c.content for c in text_chunks],state.active_profile)
        candidate_embeddings = (await embed_documents(embedder,[c.content for c in text_chunks],state.candidate_profile)
                                if state.candidate_profile else [None]*len(text_chunks))
    except Exception as exc:
        raise IngestionError(f"Embedding failed: {exc}") from exc

    if len(embeddings) != len(text_chunks) or any(len(v) != settings.embedding_dim for v in embeddings):
        raise IngestionError("Embedding count or dimensions do not match")

    # Change the stored document only after successful embedding.
    if existing:
        doc = existing
        await db.execute(delete(Chunk).where(Chunk.document_id == doc.id))
        await db.flush()
    else:
        doc = Document(source_type=source_type, external_id=external_id)
        db.add(doc)
    doc.title = title
    doc.source_url = source_url
    doc.meta = {**((doc.meta or {}) if meta is None else meta),
                '_embedding_profiles':{fingerprint(p):content_hash for p in profiles},'_chunking_profile':chunking_profile}
    doc.content_hash = content_hash
    doc.status = DocumentStatus.PENDING
    await db.flush()

    # 4. Сохраняем чанки
    for tc, emb, candidate_emb in zip(text_chunks, embeddings,candidate_embeddings):
        db.add(
            Chunk(
                document_id=doc.id,
                chunk_index=tc.index,
                content=tc.content,
                embedding=emb,
                embedding_candidate=candidate_emb,
                meta=tc.meta,
                token_count=tc.meta.get('token_count',len(tc.content) // 4),
            )
        )

    doc.status = DocumentStatus.INDEXED
    await db.commit()

    return {
        "document_id": doc.id,
        "status": "updated" if existing else "created",
        "chunks_created": len(text_chunks),
    }


async def ingest_url(
        db: AsyncSession,
        *,
        url: str,
        source_type: str = "web",
        meta: dict | None = None,
        embedder: EmbeddingProvider,
        settings: Settings,
) -> dict:
    """Fetch → extract text → ingest_text."""
    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            r = await client.get(url)
            r.raise_for_status()
            html = r.text
    except Exception as exc:
        raise IngestionError(f"Failed to fetch {url}: {exc}") from exc

    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "noscript"]):
        tag.decompose()

    text = soup.get_text("\n", strip=True)
    title = (soup.title.string.strip() if soup.title and soup.title.string else url)

    return await ingest_text(
        db,
        title=title,
        text=text,
        source_type=source_type,
        source_url=url,
        external_id=url,
        meta=meta or {},
        embedder=embedder,
        settings=settings,
    )
