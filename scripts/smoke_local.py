"""Verify real local embeddings, retrieval, chat, and session persistence.

Adds one clearly labelled synthetic document, never university facts.
Run from the repository: .venv\Scripts\python -m scripts.smoke_local
"""
import asyncio
import json
import sys
from pathlib import Path

import httpx

from src.core.config import settings
from src.core.db.session import AsyncSessionLocal, engine
from src.core.rag.embeddings import build_embedder
from src.core.rag.ingest import ingest_text
from src.core.rag.retriever import HybridRetriever


async def main():
    embedder = build_embedder(settings)
    vectors = await embedder.embed_many(["Проверка локальной модели"])
    assert len(vectors[0]) == 768
    async with AsyncSessionLocal() as db:
        ingested = await ingest_text(
            db, title="ТЕСТ: консультация по математическому анализу",
            text="Это вымышленные тестовые данные, не расписание института. "
                 "Тестовая консультация по математическому анализу состоится "
                 "в среду в 15:00 в аудитории 312.",
            source_type="test", external_id="local-smoke-math-v1",
            embedder=embedder, settings=settings,
        )
        hits = await HybridRetriever(db, embedder, settings, include_test=True).search(
            "Когда тестовая консультация по математическому анализу?"
        )
        assert hits and "312" in hits[0].content
    async with httpx.AsyncClient(base_url="http://127.0.0.1:8000", timeout=240) as client:
        for route in ("/health", "/ready", "/widget/", "/docs"):
            response = await client.get(route)
            response.raise_for_status()
        response = await client.post("/api/v1/chat", json={
            "message": "Кто директор колледжа СПК?",
        })
        response.raise_for_status()
        chat = response.json()
        assert chat["sources"] and "Гурина" in chat["answer"], chat
        response = await client.post("/api/v1/chat", json={
            "intent": "local_smoke_menu", "session_id": chat["session_id"],
        })
        response.raise_for_status()
        assert response.json()["session_id"] == chat["session_id"]
        assert response.json()["message_id"] > chat["message_id"]
    report = {"embedding_dim": len(vectors[0]), "ingestion": ingested,
              "retrieval_sources": [h.source for h in hits], "chat": chat,
              "session_persistence": True}
    Path("logs/smoke-result.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    await engine.dispose()


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    asyncio.run(main())
