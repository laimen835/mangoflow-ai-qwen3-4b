import json
from uuid import uuid4
import httpx
import pytest
from src.core.share import create_app


@pytest.fixture
def gateway():
    calls=[]
    def upstream(request):
        body=json.loads(request.content) if request.content else None
        calls.append((request.method,request.url.path,body))
        if request.url.path=='/api/v1/portal/sources':
            return httpx.Response(200,json={'items':[
                {'id':1,'is_test':True,'url':None},
                {'id':2,'is_test':False,'url':'https://ncsa.ru/document.pdf'},
                {'id':3,'is_test':False,'url':None}]})
        if request.url.path=='/api/v1/chat':
            return httpx.Response(200,json={'session_id':body['session_id'],'answer':'Ответ','message_id':1})
        if request.url.path=='/api/v1/chat/stream':
            events=[{'type':'delta','text':'Ответ'},
                    {'type':'done','data':{'session_id':body['session_id'],'answer':'Ответ'}}]
            return httpx.Response(200,content='\n'.join(json.dumps(e) for e in events)+'\n')
        if request.url.path.startswith('/api/v1/portal/conversations/'):
            return httpx.Response(200,json={'items':[{'content':'Guest history'}]})
        return httpx.Response(200,json={'saved':True})
    client=httpx.AsyncClient(transport=httpx.MockTransport(upstream),base_url='http://127.0.0.1:8000')
    return create_app(client),calls,client


@pytest.mark.asyncio
async def test_public_sessions_never_reuse_local_ids_and_are_isolated(gateway):
    app,calls,upstream=gateway
    old_local_id=str(uuid4())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='https://demo.test') as a:
        r=await a.post('/api/v1/chat',json={'message':'Контакты','session_id':old_local_id})
        assert r.status_code==200 and r.json()['session_id']==old_local_id
        private=calls[-1][2]['session_id']
        assert private!=old_local_id
        assert (await a.get('/api/v1/portal/conversations/'+old_local_id)).json()['items']
        assert calls[-1][1].endswith(private)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='https://demo.test') as b:
            before=len(calls)
            assert (await b.get('/api/v1/portal/conversations/'+old_local_id)).json()=={'items':[]}
            assert len(calls)==before
            assert (await b.post('/api/v1/portal/messages/1/feedback',json={'session_id':old_local_id,'rating':1})).status_code==404
        cookie=r.headers['set-cookie'].lower()
        assert 'secure' in cookie and 'httponly' in cookie and 'samesite=lax' in cookie
    await upstream.aclose()


@pytest.mark.asyncio
async def test_stream_ids_are_mapped_and_admin_paths_never_forwarded(gateway):
    app,calls,upstream=gateway
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='https://demo.test') as c:
        public=str(uuid4())
        r=await c.post('/api/v1/chat/stream',json={'message':'Контакты','session_id':public})
        events=[json.loads(s) for s in r.text.splitlines()]
        assert events[-1]['data']['session_id']==public
        assert calls[-1][2]['session_id']!=public
        before=len(calls)
        for path in ['/docs','/openapi.json','/api/v1/admin/sync','/api/v1/admin/search/debug','/.env','/widget/.env','/api/tags']:
            assert (await c.get(path)).status_code in (404,405)
            assert (await c.post(path,json={})).status_code in (404,405)
        assert len(calls)==before
    await upstream.aclose()


@pytest.mark.asyncio
async def test_local_sources_and_files_hidden(gateway):
    app,_,upstream=gateway
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='https://demo.test') as c:
        assert [d['id'] for d in (await c.get('/api/v1/portal/sources')).json()['items']]==[2]
        assert (await c.get('/api/v1/files/1')).status_code==404
        assert (await c.get('/api/v1/files/3')).status_code==404
        assert (await c.get('/api/v1/files/2')).headers['location']=='https://ncsa.ru/document.pdf'
    await upstream.aclose()


@pytest.mark.asyncio
async def test_write_validation_and_rate_limit(gateway):
    app,calls,upstream=gateway
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='https://demo.test') as c:
        assert (await c.post('/api/v1/chat',json={'message':'Hi'},headers={'Origin':'https://other.test'})).status_code==403
        assert (await c.post('/api/v1/chat',content='bad')).status_code==415
        assert (await c.post('/api/v1/chat',json={'message':'x'*40000})).status_code==413
        session=str(uuid4())
        for _ in range(20):
            assert (await c.post('/api/v1/chat',json={'message':'Hi','session_id':session})).status_code==200
        assert (await c.post('/api/v1/chat',json={'message':'Hi','session_id':session})).status_code==429
    await upstream.aclose()
