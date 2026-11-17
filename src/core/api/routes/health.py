from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.api.schemas import HealthResponse, ReadyResponse
from src.core.db.session import get_db
from src.core.config import settings
import httpx

router = APIRouter(tags=["health"])

APP_VERSION = "0.2.0"


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    """Liveness-проба: процесс жив. Не проверяет зависимости."""
    return HealthResponse(status="ok", version=APP_VERSION)


@router.get("/ready", response_model=ReadyResponse)
async def ready(db: AsyncSession = Depends(get_db)) -> ReadyResponse:
    """
    Readiness-проба: можно ли принимать трафик.
    Проверяет БД (обязательно). Проверку LLM можно добавить с кэшем на 30 с.
    """
    db_ok = True
    try:
        await db.execute(text("SELECT 1"))
    except Exception:
        db_ok = False

    llm_ok = False
    try:
        async with httpx.AsyncClient(timeout=3) as client:
            if settings.llm_provider == "ollama":
                response = await client.get(settings.ollama_base_url.rstrip("/") + "/api/tags")
                response.raise_for_status()
                names = {m["name"] for m in response.json().get("models", [])}
                expected = {m if ":" in m else m + ":latest" for m in (settings.llm_model, settings.embedding_model)}
                llm_ok = expected.issubset(names)
            else:
                response = await client.get(settings.openai_base_url.rstrip("/") + "/models",
                    headers={"Authorization": "Bearer " + settings.openai_api_key})
                llm_ok = response.is_success
    except (httpx.HTTPError, ValueError, KeyError):
        pass
    overall = "ready" if (db_ok and llm_ok) else "degraded"

    return ReadyResponse(status=overall, db=db_ok, llm=llm_ok)
