import httpx
import pytest
from src.core.main import app
from src.core.api import deps
from src.core.api.routes import admin
from src.core.db.session import get_db
from src.core.rag.retriever import RetrievalResult


@pytest.mark.asyncio
async def test_debug_requires_admin_key_and_returns_retrieval_diagnostics(monkeypatch):
    monkeypatch.setattr(deps,'settings',deps.settings.model_copy(update={'admin_api_key':'unit-test-key'}))
    class Retriever:
        def __init__(self,*a,**kw):pass
        async def retrieve(self,q):return RetrievalResult(status='no_match',diagnostics={'query_seen':q})
    monkeypatch.setattr(admin,'HybridRetriever',Retriever)
    from types import SimpleNamespace
    async def scalars(*args):return SimpleNamespace(all=lambda:[])
    async def no_db():yield SimpleNamespace(scalars=scalars)
    app.dependency_overrides[get_db]=no_db
    app.dependency_overrides[deps.get_embedder]=lambda:None
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as c:
            assert (await c.post('/api/v1/admin/search/debug',json={'query':'Вопрос'})).status_code==401
            r=await c.post('/api/v1/admin/search/debug',headers={'X-Admin-Key':'unit-test-key'},
                json={'query':'А в 2027?','history':[{'role':'user','content':'Сроки поступления в 2026?'}]})
            assert r.status_code==200 and '2027' in r.json()['query'] and '2026' not in r.json()['query']
            assert r.json()['status']=='no_match' and 'diagnostics' in r.json()
    finally:
        app.dependency_overrides.pop(get_db,None);app.dependency_overrides.pop(deps.get_embedder,None)
