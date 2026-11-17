import asyncio
from datetime import datetime, timezone

import httpx
import pytest

from src.core.db.models import Document, DocumentStatus
from src.core.rag.ncsa_source import archive_missing
from src.core.services import updates


@pytest.mark.asyncio
@pytest.mark.parametrize("fresh", [True, False])
async def test_auto_update_only_when_due(monkeypatch, fresh):
    called = []
    monkeypatch.setattr(updates, "read_json", lambda *a: {"completed_at": datetime.now(timezone.utc).isoformat()} if fresh else {})
    async def update():
        called.append(True)
    async def stop_after_one_iteration(_):
        raise asyncio.CancelledError
    monkeypatch.setattr(updates, "run_update", update)
    monkeypatch.setattr(updates.asyncio, "sleep", stop_after_one_iteration)
    with pytest.raises(asyncio.CancelledError):
        await updates.scheduled_updates()
    assert bool(called) is not fresh


@pytest.mark.asyncio
async def test_temporary_site_failure_preserves_document_but_404_archives(db):
    url = "https://ncsa.ru/institutes/spk/test-archive-regression"
    doc = Document(title="ТЕСТ архивации", source_type="ncsa_web", external_id=url,
                   source_url=url, status=DocumentStatus.INDEXED, meta={})
    db.add(doc)
    await db.flush()
    request = httpx.Request("GET", url)
    for status, expected in [(500, DocumentStatus.INDEXED), (404, DocumentStatus.ARCHIVED)]:
        response = httpx.Response(status, request=request)
        await archive_missing(db, url, httpx.HTTPStatusError("failure", request=request, response=response))
        assert doc.status == expected
