from __future__ import annotations

from datetime import datetime
from uuid import UUID
from zoneinfo import ZoneInfo
from urllib.parse import urlsplit
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import settings
from src.core.db.models import Chunk, Conversation, Document, DocumentStatus, Message, MessageRole
from src.core.db.session import get_db
from src.core.rag.ncsa_source import SEED
from src.core.services.directory import find_teachers, week_status
from src.core.rag.document_policy import is_test_document,exclusion_reasons
from src.core.services.updates import REPORT_FILE, STATUS_FILE, read_json

router = APIRouter(prefix="/portal", tags=["portal"])


async def seed_document(db):
    return (await db.execute(select(Document).where(Document.external_id == SEED,
        Document.source_type == "ncsa_web", Document.status == DocumentStatus.INDEXED))).scalar_one_or_none()


def document_json(doc):
    meta = doc.meta or {}
    return {"id": doc.id, "title": doc.title, "url": doc.source_url, "type": doc.source_type,
            "format":Path(urlsplit(doc.source_url or '').path).suffix.lstrip('.').lower() or 'html',
            "checked_at": meta.get("fetched_at"), "pages": meta.get("pages"),
            "extraction": meta.get("extraction", "text"), "groups": meta.get("groups", []),
            "is_test": is_test_document(doc),
            "needs_verification": bool(exclusion_reasons(doc)) or (meta.get("extraction") == "ocr" and not meta.get('review',{}).get('text_verified')),
            "review":{k:v for k,v in meta.get('review',{}).items() if k in {'scope','currency','valid_from','valid_to','reviewed_at'}},
            "exclusion_reasons":exclusion_reasons(doc),"extraction_version":meta.get('extraction_version'),
            "download_url": f"/api/v1/files/{doc.id}" if doc.file_path else None}


@router.get("/overview")
async def overview(db: AsyncSession = Depends(get_db)):
    docs = (await db.execute(select(Document).where(Document.status == DocumentStatus.INDEXED))).scalars().all()
    website = [d for d in docs if d.source_type.startswith("ncsa_")]
    seed = next((d for d in website if d.external_id == SEED), None)
    now = datetime.now(ZoneInfo("Europe/Moscow"))
    return {"date": now.date().isoformat(), "timezone": "Europe/Moscow",
        "documents": len(docs), "website_documents": len(website), "local_documents": len(docs) - len(website),
        "chunks": await db.scalar(select(func.count(Chunk.id)).join(Document).where(Document.status == DocumentStatus.INDEXED)),
        "teachers": len(seed.meta.get("teachers", [])) if seed else 0,
        "contacts": seed.meta.get("contacts") if seed else None,
        "source_url": SEED, "checked_at": seed.meta.get("fetched_at") if seed else None,
        "week": week_status(now.date(), settings.academic_week_anchor, settings.academic_week_anchor_type, settings.academic_week_source),
        "auto_sync": {"enabled": settings.auto_sync_enabled, "interval_hours": settings.sync_interval_hours},
        "update": read_json(STATUS_FILE, {"state": "idle"}),
        "import_errors": len(read_json(REPORT_FILE, {}).get("errors", []))}


@router.get("/sources")
async def sources(q: str = Query("", max_length=200), db: AsyncSession = Depends(get_db)):
    docs = (await db.execute(select(Document).where(Document.status == DocumentStatus.INDEXED)
                            .order_by(Document.id))).scalars().all()
    return {"items": [document_json(d) for d in docs if q.casefold() in d.title.casefold()]}


@router.get("/source-status")
async def source_status():
    report = read_json(REPORT_FILE, {})
    scholarship_report=read_json('logs/scholarship-sync.json',{})
    errors = []
    for item in report.get("errors", [])+scholarship_report.get('errors',[]):
        raw = str(item.get("error", item.get('reason','')))
        reason = ('Превышен лимит страниц для OCR' if 'OCR limit' in raw else
                  'Нужна конвертация старого формата Office' if 'requires conversion' in raw else
                  "Недостаточно читаемого текста после OCR" if "OCR" in raw else
                  "Документ превышает установленный предел страниц" if "150 pages" in raw else
                  "Сайт вернул ошибку HTTP" if "HTTP" in raw or "status" in raw.lower() else
                  "Материал не удалось обработать")
        url = str(item.get("url", ""))
        if url.startswith("https://ncsa.ru/"):
            errors.append({"url": url, "reason": reason})
    return {"checked_at": report.get("completed_at"), "errors": errors,
            "remaining_pages": report.get("remaining_page_links", 0),
            "pdfs_discovered": report.get("pdfs_discovered", 0)}


@router.get("/teachers")
async def teachers(q: str = Query("", max_length=100), department: str = Query("", max_length=100), db: AsyncSession = Depends(get_db)):
    doc = await seed_document(db)
    people = doc.meta.get("teachers", []) if doc else []
    if department:
        people = [p for p in people if department in p["departments"]]
    found, fuzzy = find_teachers(people, q)
    return {"items": found[:150], "total": len(people), "fuzzy": fuzzy, "source_url": SEED,
            "checked_at": doc.meta.get("fetched_at") if doc else None}


@router.get("/schedule")
async def schedule(group: str = Query("", max_length=50), db: AsyncSession = Depends(get_db)):
    docs = (await db.execute(select(Document).where(Document.source_type == "ncsa_pdf",
        Document.status == DocumentStatus.INDEXED).order_by(Document.id))).scalars().all()
    docs = [d for d in docs if "расписан" in d.title.casefold()]
    groups = sorted(set(g for d in docs for g in d.meta.get("groups", [])))
    found = [d for d in docs if not group or group in d.meta.get("groups", [])]
    return {"items": [document_json(d) for d in found], "groups": groups,
            "lessons_verified": False,
            "note": "Официальные PDF. Автоматическое распределение конкретных пар по дням ещё не подтверждено."}


@router.get("/conversations/{session_id}")
async def conversation(session_id: UUID, db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(Message).join(Conversation).where(Conversation.session_id == session_id)
        .order_by(Message.id.desc()).limit(100))).scalars().all()
    return {"items": [{"id": m.id, "role": m.role.value, "content": m.content,
             "sources": m.sources or [], "usage": m.usage or {}, "created_at": m.created_at.isoformat(),
             "legacy": m.role == MessageRole.ASSISTANT and not (m.usage or {}).get("knowledge_revision")}
            for m in reversed(rows)]}


class Feedback(BaseModel):
    session_id: UUID
    rating: int = Field(ge=-1, le=1)


@router.post("/messages/{message_id}/feedback")
async def feedback(message_id: int, payload: Feedback, db: AsyncSession = Depends(get_db)):
    msg = (await db.execute(select(Message).join(Conversation).where(Message.id == message_id,
        Conversation.session_id == payload.session_id, Message.role == MessageRole.ASSISTANT))).scalar_one_or_none()
    if msg is None:
        raise HTTPException(404, "Message not found in this conversation")
    msg.usage = {**(msg.usage or {}), "feedback": payload.rating}
    await db.commit()
    return {"saved": True, "rating": payload.rating}
