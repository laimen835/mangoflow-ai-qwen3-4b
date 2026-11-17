from __future__ import annotations

from dataclasses import dataclass,field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.db.models import Chunk, Document
from src.core.rag.exceptions import RetrievalError


@dataclass
class SearchHit:
    """Единый формат результата поиска (для обоих видов)."""
    chunk_id: int
    document_id: int
    content: str
    title: str
    url: str | None
    rank: int
    raw_score: float
    source: str  # "vector" | "bm25"
    rerank_score: float | None = None
    meta: dict = field(default_factory=dict)


async def vector_search(
    db: AsyncSession,
    query_embedding: list[float],
    limit: int = 20,
    min_similarity: float = 0.25,
    include_test: bool = False,
    unit_keys: tuple[str, ...] = (),
    candidate: bool = False,
    document_ids: tuple[int, ...] = (),
) -> list[SearchHit]:
    """
    Возвращает top-N чанков по косинусной близости.
    1 - cosine_distance = similarity (1 = идентичны).
    """
    try:
        column = Chunk.embedding_candidate if candidate else Chunk.embedding
        distance = column.cosine_distance(query_embedding).label("distance")
        stmt = (
            select(Chunk, Document, distance)
            .join(Document, Document.id == Chunk.document_id)
            .where(column.is_not(None))
            .where(Document.status == "INDEXED")  # ищем только по проиндексированным
            .order_by(distance)
            .limit(limit)
        )
        if unit_keys:
            from src.core.services.academy_scope import unit_filter
            stmt = stmt.where(unit_filter(unit_keys))
        if document_ids:
            stmt = stmt.where(Document.id.in_(document_ids))
        if not include_test:
            from src.core.rag.document_policy import real_document_filter
            stmt = stmt.where(real_document_filter())
        rows = (await db.execute(stmt)).all()
    except Exception as exc:
        raise RetrievalError(f"Vector search failed: {exc}") from exc

    hits: list[SearchHit] = []
    for rank, (chunk, doc, dist) in enumerate(rows, start=1):
        similarity = 1.0 - float(dist)
        if similarity < min_similarity:
            continue
        hits.append(
            SearchHit(
                chunk_id=chunk.id,
                document_id=doc.id,
                content=chunk.content,
                title=doc.title,
                url=doc.source_url,
                rank=rank,
                raw_score=similarity,
                source="vector",
                meta={**(chunk.meta or {}),'unit_key':(doc.meta or {}).get('unit_key'),'unit_name':(doc.meta or {}).get('unit_name'),'chunk_index':chunk.chunk_index,'document_review':(doc.meta or {}).get('review'),
                      'scholarship_material':(doc.meta or {}).get('scholarship_material',False)},
            )
        )
    return hits
