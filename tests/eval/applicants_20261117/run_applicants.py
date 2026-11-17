"""Run a frozen applicant set through the real ChatService, isolated DB only."""
import argparse
import asyncio
import hashlib
import json
import platform
import subprocess
import time
from pathlib import Path
from uuid import uuid4

import httpx
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.core.config import settings
from src.core.api.schemas import ChatRequest
from src.core.db.models import MessageRole
from src.core.llm.ollama_client import OllamaClient
from src.core.rag.embeddings import build_embedder
from src.core.rag.retriever import HybridRetriever
from src.core.services.chat_service import ChatService
from src.core.services import history
from scripts.evaluate_deep import corpus_stamp, code_stamp

ROOT = Path(__file__).resolve().parent
APP = Path(r'D:\Codex-RAG-runtime\qwen35-9b-20261117\app')
MODELS = {'4b': (11534, 'qwen3:4b-instruct'), '9b': (11535, 'qwen3.5:9b-q4_K_M')}


class Recorded:
    def __init__(self, inner):
        self.inner = inner
        self.model_name = inner.model_name
        self.calls = []

    async def generate_structured(self, messages, schema, **kwargs):
        start = time.perf_counter()
        record = {'kind': 'structured', 'messages': messages, 'schema': schema,
                  'kwargs': kwargs}
        self.calls.append(record)
        try:
            response = await self.inner.generate_structured(messages, schema, **kwargs)
            record.update(text=response.text, usage=response.usage)
            return response
        except Exception as exc:
            record['error'] = type(exc).__name__
            raise
        finally:
            record['wall_ms'] = round((time.perf_counter()-start)*1000, 2)

    async def generate(self, messages, **kwargs):
        start = time.perf_counter()
        record = {'kind': 'generate', 'messages': messages, 'kwargs': kwargs}
        self.calls.append(record)
        try:
            response = await self.inner.generate(messages, **kwargs)
            record.update(text=response.text, usage=response.usage)
            return response
        finally:
            record['wall_ms'] = round((time.perf_counter()-start)*1000, 2)

    async def stream(self, messages, **kwargs):
        start = time.perf_counter()
        record = {'kind': 'stream', 'messages': messages, 'kwargs': kwargs, 'text': ''}
        self.calls.append(record)
        try:
            async for event in self.inner.stream(messages, **kwargs):
                record['text'] += event.get('delta', '')
                yield event
        finally:
            record['wall_ms'] = round((time.perf_counter()-start)*1000, 2)


def save(path, report):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(path)


async def main(label):
    output = ROOT/f'answers-{label}.json'
    if output.exists():
        raise RuntimeError('Refusing to overwrite a model run')
    url = make_url(settings.database_url)
    assert url.host == '127.0.0.1' and url.port == 55433
    assert url.database == 'rag_admissions_eval_20261117'
    cases_path = ROOT/'cases-frozen.json'
    digest = hashlib.sha256(cases_path.read_bytes()).hexdigest()
    assert digest == (ROOT/'cases-sha256.txt').read_text(encoding='ascii')
    cases = json.loads(cases_path.read_text(encoding='utf-8'))['cases']
    assert len(cases) == 60
    port, model = MODELS[label]
    cfg = settings.model_copy(update={'llm_provider': 'ollama', 'llm_model': model,
        'ollama_base_url': f'http://127.0.0.1:{port}', 'ollama_think': False,
        'ollama_num_ctx': 4096, 'llm_timeout_s': 180,
        'openai_base_url': 'http://127.0.0.1:11534/v1',
        'retrieval_policy': 'multi_query', 'multi_query_rerank': True,
        'academy_rerank_enabled': False, 'auto_sync_enabled': False,
        'deep_crawl_enabled': False, 'answer_mode': 'source_quotes'})
    report = {'status': 'running', 'model': model, 'label': label, 'case_sha256': digest,
        'code_before': code_stamp(), 'runner_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'source_commit': subprocess.check_output(['git','rev-parse','HEAD'], cwd=APP, text=True).strip(),
        'database': url.database, 'python': platform.python_version(), 'rows': [],
        'settings': {'mode': cfg.answer_mode, 'policy': cfg.retrieval_policy,
            'rerank': True, 'think': False, 'context': 4096, 'selector_temperature': 0,
            'selector_max_tokens': 160, 'embedding': cfg.embedding_model,
            'embedding_endpoint': cfg.openai_base_url,
            'note': 'Model-native sampling defaults remain unchanged; this compares deployed variants.'}}
    engine = create_async_engine(url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    embedder = build_embedder(cfg)
    recorder = Recorded(OllamaClient(cfg))
    try:
        async with httpx.AsyncClient(timeout=60, trust_env=False) as client:
            # Only inference caches are released, never services/configuration/data.
            other = '9b' if label == '4b' else '4b'
            other_port, other_model = MODELS[other]
            response = await client.post(f'http://127.0.0.1:{other_port}/api/generate',
                json={'model': other_model, 'stream': False, 'keep_alive': 0})
            response.raise_for_status()
            report['other_model_cache_released'] = response.json().get('done_reason')
            report['model_details'] = (await client.post(f'http://127.0.0.1:{port}/api/show',
                json={'model': model})).json().get('details')
            tags = (await client.get(f'http://127.0.0.1:{port}/api/tags')).json()['models']
            report['model_digest'] = next(m['digest'] for m in tags if m['name'] == model)
        async with sessions() as db:
            report['corpus_before'] = await corpus_stamp(db)
            assert report['corpus_before']['sha256'] == 'aa7779d8165d5b79de988339be2d25e07df696c37a26e5653fec0e4588175a62'
            warm_retriever = HybridRetriever(db, embedder, cfg)
            await warm_retriever.retrieve('Телефон приёмной комиссии академии')
        start = time.perf_counter()
        await recorder.inner.generate_structured([
            {'role':'system','content':'Верни только JSON по заданной схеме.'},
            {'role':'user','content':'Для прогрева верни пустой список sources и complete=false.'}],
            {'type':'object','properties':{'sources':{'type':'array','items':{'type':'integer'}},
                'complete':{'type':'boolean'}},'required':['sources','complete'],'additionalProperties':False},
            max_tokens=40)
        report['excluded_warmup_ms'] = round((time.perf_counter()-start)*1000, 2)
        save(output, report)
        for case in cases:
            recorder.calls = []
            start = time.perf_counter(); first = None; done = None; events = []
            row = {'id': case['id'], 'question': case['question'], 'category': case['category']}
            try:
                async with sessions() as db:
                    session_id = uuid4()
                    if case['history']:
                        conversation, _ = await history.get_or_create_conversation(db, session_id, 'api', None)
                        for turn in case['history']:
                            await history.append_message(db, conversation, MessageRole.USER, turn['content'])
                        await db.commit()
                    service = ChatService(db, recorder, HybridRetriever(db, embedder, cfg), cfg)
                    async for event in service.stream(ChatRequest(session_id=session_id, message=case['question'])):
                        events.append(event['type'])
                        if event['type'] == 'delta' and first is None:
                            first = round((time.perf_counter()-start)*1000, 2)
                        if event['type'] == 'done':
                            done = event['data']
                row.update(response=done, events=events)
                if done is None: row['error'] = 'No final answer'
            except Exception as exc:
                row['error'] = type(exc).__name__
            row.update(total_ms=round((time.perf_counter()-start)*1000, 2),
                       first_text_ms=first, calls=recorder.calls)
            for call in row['calls']:
                call['messages_sha256'] = hashlib.sha256(json.dumps(call['messages'],
                    ensure_ascii=False, sort_keys=True).encode()).hexdigest()
            report['rows'].append(row); save(output, report)
            print(label, len(report['rows']), '/', len(cases), case['id'],
                  (done or {}).get('usage',{}).get('route'), row['total_ms'],
                  'calls', len(row['calls']), flush=True)
        async with sessions() as db:
            report['corpus_after'] = await corpus_stamp(db)
        report['code_after'] = code_stamp()
        report['corpus_unchanged'] = report['corpus_before'] == report['corpus_after']
        report['code_unchanged'] = report['code_before'] == report['code_after']
        assert report['corpus_unchanged'] and report['code_unchanged']
        async with httpx.AsyncClient(timeout=10, trust_env=False) as client:
            report['model_residency'] = {}
            for service_port in (11534,11535):
                report['model_residency'][str(service_port)] = (await client.get(
                    f'http://127.0.0.1:{service_port}/api/ps')).json()
        report['status'] = 'complete'; save(output, report)
        print('DONE', label, len(report['rows']), flush=True)
    finally:
        await engine.dispose()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--model', required=True, choices=MODELS)
    asyncio.run(main(parser.parse_args().model))
