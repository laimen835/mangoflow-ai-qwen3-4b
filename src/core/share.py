"""Limited guest gateway for a temporary internet demo.

Run on loopback only; cloudflared connects to this port, never to the full API.
Guest conversation IDs map to fresh private upstream IDs. Local conversations,
administration, API docs and arbitrary upstream paths are not exposed.
"""
from __future__ import annotations
import asyncio
from collections import deque
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
import json
from pathlib import Path
import secrets
import time
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
import httpx

from src.core.api.schemas import ChatRequest

WIDGET = Path(__file__).resolve().parents[2] / 'widget'
COOKIE = 'spk_guest'
PUBLIC_GET = {'overview', 'sources', 'teachers', 'schedule', 'source-status'}


@dataclass
class Guest:
    sessions: dict[str, str] = field(default_factory=dict)
    requests: deque = field(default_factory=deque)
    touched: float = field(default_factory=time.monotonic)


def official(url):
    try:
        return urlsplit(url or '').scheme == 'https' and urlsplit(url).hostname in {'ncsa.ru','www.ncsa.ru'}
    except ValueError:
        return False


def create_app(upstream=None):
    visitors = {}
    global_requests = deque()
    slots = asyncio.Semaphore(2)

    @asynccontextmanager
    async def lifespan(app):
        yield
        if upstream is None:
            await client.aclose()

    client = upstream or httpx.AsyncClient(base_url='http://127.0.0.1:8000', timeout=240,
                                         trust_env=False, follow_redirects=False)
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)

    @app.middleware('http')
    async def guest_cookie(request, call_next):
        now=time.monotonic()
        for key in list(visitors):
            if now-visitors[key].touched > 86400:
                del visitors[key]
        token=request.cookies.get(COOKIE)
        new=token not in visitors
        if new:
            if len(visitors)>=1024:
                return JSONResponse({'detail':'Демо занято. Попробуйте позже.'},503)
            token=secrets.token_urlsafe(32)
            visitors[token]=Guest()
        request.state.guest=visitors[token]
        request.state.guest.touched=now
        response=await call_next(request)
        if new:
            response.set_cookie(COOKIE,token,secure=True,httponly=True,samesite='lax',max_age=86400)
        response.headers['X-Content-Type-Options']='nosniff'
        response.headers['Referrer-Policy']='strict-origin-when-cross-origin'
        response.headers['Cache-Control']='no-store'
        return response

    async def payload(request):
        origin=request.headers.get('origin')
        if origin and urlsplit(origin).netloc != request.headers.get('host'):
            raise HTTPException(403,'Cross-origin writes are disabled')
        if request.headers.get('content-type','').split(';')[0] != 'application/json':
            raise HTTPException(415,'Use application/json')
        body=bytearray()
        async for part in request.stream():
            body.extend(part)
            if len(body)>32768:
                raise HTTPException(413,'Request too large')
        try:
            value=json.loads(body)
            if not isinstance(value,dict):raise ValueError()
            return value
        except (ValueError,TypeError):
            raise HTTPException(400,'Invalid JSON')

    def session(guest, public_id=None, create=False):
        try:
            key=str(UUID(str(public_id))) if public_id else str(uuid4())
        except ValueError:
            raise HTTPException(422,'Invalid session')
        if key not in guest.sessions:
            if not create:return key,None
            if len(guest.sessions)>=24:raise HTTPException(429,'Too many demo conversations')
            guest.sessions[key]=str(uuid4())
        return key,guest.sessions[key]

    def limit(guest):
        now=time.monotonic()
        for entries in (guest.requests,global_requests):
            while entries and now-entries[0]>600:entries.popleft()
        if len(guest.requests)>=20 or len(global_requests)>=120:
            raise HTTPException(429,'Достигнут лимит пробного доступа. Повторите позже.',headers={'Retry-After':'60'})
        guest.requests.append(now);global_requests.append(now)

    @app.get('/')
    async def home():
        return RedirectResponse('/widget/demo.html')

    @app.get('/widget/')
    async def portal():
        return HTMLResponse((WIDGET/'index.html').read_text(encoding='utf-8').replace(
            'id="refresh-button"','id="refresh-button" hidden'))

    @app.get('/widget/{name}')
    async def static(name):
        if name == 'mangoflow-logo.png':
            return FileResponse(WIDGET/name, media_type='image/png')
        if Path(name).name!=name or Path(name).suffix not in {'.html','.css','.js'} or not (WIDGET/name).is_file():
            raise HTTPException(404)
        if name=='index.html':return await portal()
        return FileResponse(WIDGET/name)

    async def get(path, params=None):
        try:
            response=await client.get(path,params=params)
            response.raise_for_status()
            return response.json()
        except (httpx.HTTPError,ValueError):
            raise HTTPException(503,'Локальный сервер временно недоступен')

    @app.get('/ready')
    async def ready():
        data=await get('/ready')
        return {k:data.get(k) for k in ('status','db','llm')}

    @app.get('/api/v1/portal/conversations/{public_id}')
    async def history(public_id:UUID, request:Request):
        _,private_id=session(request.state.guest,public_id)
        if not private_id:return {'items':[]}
        return await get('/api/v1/portal/conversations/'+private_id)

    @app.get('/api/v1/portal/{name}')
    async def directory(name,request:Request):
        if name not in PUBLIC_GET:raise HTTPException(404)
        data=await get('/api/v1/portal/'+name,request.query_params)
        if name=='sources':
            data['items']=[d for d in data.get('items',[]) if not d.get('is_test') and official(d.get('url'))]
        if name=='overview':
            data={k:v for k,v in data.items() if k in {'date','timezone','website_documents','teachers',
                'contacts','source_url','checked_at','week','auto_sync','import_errors','chunks'}}
            data.update(local_documents=0,update={'state':'idle'})
        return data

    @app.post('/api/v1/portal/messages/{message_id}/feedback')
    async def feedback(message_id:int,request:Request):
        data=await payload(request)
        _,private_id=session(request.state.guest,data.get('session_id'))
        if not private_id:raise HTTPException(404)
        data['session_id']=private_id
        r=await client.post(f'/api/v1/portal/messages/{message_id}/feedback',json=data)
        if not r.is_success:raise HTTPException(r.status_code,'Feedback unavailable')
        return r.json()

    @app.get('/api/v1/files/{document_id}')
    async def file(document_id:int):
        data=await get('/api/v1/portal/sources')
        doc=next((d for d in data.get('items',[]) if d['id']==document_id and not d.get('is_test') and official(d.get('url'))),None)
        if not doc:raise HTTPException(404)
        return RedirectResponse(doc['url'])

    @app.post('/api/v1/chat')
    @app.post('/api/v1/chat/stream')
    async def chat(request:Request):
        data=await payload(request)
        try:
            parsed=ChatRequest.model_validate(data)
        except ValueError:
            raise HTTPException(422,'Invalid chat request')
        guest=request.state.guest
        public_id,private_id=session(guest,parsed.session_id,create=True)
        limit(guest)
        data=parsed.model_dump(mode='json')
        data.update(session_id=private_id,channel='web',user_ref=None,top_k=None)
        try:
            await asyncio.wait_for(slots.acquire(),timeout=0.1)
        except TimeoutError:
            raise HTTPException(429,'Помощник занят. Повторите вопрос через несколько секунд.',headers={'Retry-After':'15'})
        if request.url.path.endswith('/stream'):
            async def events():
                try:
                    async with client.stream('POST','/api/v1/chat/stream',json=data) as r:
                        r.raise_for_status()
                        async for line in r.aiter_lines():
                            if not line:continue
                            event=json.loads(line)
                            if event.get('type')=='done':event['data']['session_id']=public_id
                            yield json.dumps(event,ensure_ascii=False)+'\n'
                except (httpx.HTTPError,ValueError):
                    yield json.dumps({'type':'error','message':'Сервер временно недоступен. Повторите вопрос.'},ensure_ascii=False)+'\n'
                finally:
                    slots.release()
            return StreamingResponse(events(),media_type='application/x-ndjson',headers={'X-Accel-Buffering':'no'})
        try:
            r=await client.post('/api/v1/chat',json=data)
            if not r.is_success:raise HTTPException(r.status_code,'Chat temporarily unavailable')
            result=r.json();result['session_id']=public_id
            return result
        finally:
            slots.release()

    return app


app=create_app()
