"""Frozen-case retrieval and streamed answers, with isolated database selection."""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
from pathlib import Path
import re
import time
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from src.core.config import settings
from src.core.db.models import Chunk, Document, DocumentStatus, MessageRole
from src.core.rag.document_policy import real_document_filter
from src.core.rag.embeddings import build_embedder
from src.core.rag.profiles import state_for, fingerprint
from src.core.rag.retriever import HybridRetriever
from src.core.rag.prompt import context_hits
from src.core.rag.source_context import prepare_source_hits
from src.core.rag.section_context import restore_reviewed_sections
from src.core.rag.query import contextual_query
from src.core.services.academy_scope import resolve_units
from src.core.services.dialogue_context import resolve_unit_reply
from src.core.rag.deep_crawler import atomic_json


def norm(text):
    return re.sub(r'\s+',' ',text).strip().casefold().replace('ё','е')


def p95(values):
    return sorted(values)[math.ceil(len(values)*.95)-1] if values else None


def code_stamp():
    root=Path(__file__).resolve().parents[1]
    paths=sorted((root/'src').rglob('*.py'))+[Path(__file__).resolve()]
    values={p.relative_to(root).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    return hashlib.sha256(json.dumps(values,sort_keys=True).encode()).hexdigest()


class RecordingLLM:
    def __init__(self,inner):
        self.inner=inner
        self.model_name=inner.model_name
        self.messages=[]

    async def generate(self,messages,**kwargs):
        self.messages=messages
        return await self.inner.generate(messages,**kwargs)

    async def stream(self,messages,**kwargs):
        self.messages=messages
        async for event in self.inner.stream(messages,**kwargs):
            yield event

    async def generate_structured(self,messages,*args,**kwargs):
        self.messages=messages
        return await self.inner.generate_structured(messages,*args,**kwargs)


async def corpus_stamp(db):
    rows=(await db.execute(select(Document.id,Document.content_hash,Document.title,Document.source_url,Document.meta,
        Chunk.id,Chunk.content,Chunk.meta)
        .join(Chunk).where(Document.status==DocumentStatus.INDEXED,real_document_filter()).order_by(Document.id,Chunk.id))).all()
    from src.core.rag.profiles import RetrievalIndexState
    state=await db.get(RetrievalIndexState,1)
    payload={'rows':[list(r) for r in rows],'active_profile':state.active_profile,'candidate_profile':state.candidate_profile}
    return {'sha256':hashlib.sha256(json.dumps(payload,ensure_ascii=False,sort_keys=True).encode()).hexdigest(),
            'documents':len({r[0] for r in rows}),'chunks':len(rows)}


async def evaluate(args):
    destination=Path(args.out)
    if destination.exists():
        raise ValueError('Refusing to overwrite an evaluation result')
    if args.database not in {'rag_deep_before','rag_deep_candidate'}:
        raise ValueError('This experiment only permits its two isolated databases')
    case_path=Path(args.cases)
    cases=json.loads(case_path.read_text(encoding='utf-8'))['cases']
    if args.ids:
        wanted=set(args.ids.split(','))
        cases=[c for c in cases if c['id'] in wanted]
    if args.limit:
        cases=cases[:args.limit]
    url=make_url(settings.database_url)
    if url.host not in {'127.0.0.1','localhost'} or url.port!=55433:
        raise ValueError('Expected the isolated local PostgreSQL on port 55433')
    url=url.set(database=args.database).render_as_string(hide_password=False)
    cfg=settings.model_copy(update={'database_url':url,'retrieval_policy':args.policy,
        'multi_query_rerank':args.rerank,'academy_rerank_enabled':False,'auto_sync_enabled':False,
        'answer_mode':args.answer_mode or settings.answer_mode})
    engine=create_async_engine(url)
    sessions=async_sessionmaker(engine,expire_on_commit=False)
    embedder=build_embedder(cfg)
    report={'case_sha256':hashlib.sha256(case_path.read_bytes()).hexdigest(),'database':args.database,'policy':args.policy,
            'code_before':code_stamp(),
            'rerank_enabled':args.rerank,'kind':'answers' if args.answers else 'retrieval','rows':[],
            'answer_mode':cfg.answer_mode,
            'generation_config':{'model':cfg.llm_model,'temperature':cfg.llm_temperature,
                'max_tokens':cfg.llm_max_tokens,'context':cfg.ollama_num_ctx},
            'activation_approved':False,'method':'Literal source evidence and answer markers are automatic diagnostics, not complete claim entailment or factual accuracy. Dialogue questions deliberately include difficult unresolved turns.'}
    try:
        async with sessions() as db:
            report['corpus_before']=await corpus_stamp(db)
            state=await state_for(db,cfg)
            report['active_profile']=state.active_profile
            warm=HybridRetriever(db,embedder,cfg)
            warm.unit_keys=('137',)
            await warm.retrieve('Какие направления есть в институте цифровых технологий?')
        if args.answers:
            from src.core.llm import build_llm
            llm=RecordingLLM(build_llm(cfg))
        for case in cases:
            async with sessions() as db:
                retriever=HybridRetriever(db,embedder,cfg)
                if args.answers:
                    from src.core.api.schemas import ChatRequest
                    from src.core.services.chat_service import ChatService
                    from src.core.services import history
                    session_id=uuid4()
                    if case.get('history'):
                        conversation,_=await history.get_or_create_conversation(db,session_id,'api',None)
                        for turn in case['history']:
                            # User-only anchors, no invented assistant facts.
                            await history.append_message(db,conversation,MessageRole.USER,turn['content'])
                        await db.commit()
                    service=ChatService(db,llm,retriever,cfg)
                    llm.messages=[]
                    started=time.perf_counter()
                    first=None
                    done=None
                    events=[]
                    async for event in service.stream(ChatRequest(session_id=session_id,message=case['question'])):
                        events.append(event['type'])
                        if event['type']=='delta' and first is None:
                            first=time.perf_counter()-started
                        if event['type']=='done':
                            done=event['data']
                            if first is None:
                                first=time.perf_counter()-started
                    elapsed=time.perf_counter()-started
                    row={'id':case['id'],'split':case['split'],'question':case['question'],'answerable':case['answerable'],
                         'first_text_ms':round(first*1000,2) if first is not None else None,
                         'total_ms':round(elapsed*1000,2),'events':events,'response':done,'model_messages':llm.messages}
                    if done:
                        row['expected_answer_marker']=bool(case.get('quote')) and norm(case['quote']) in norm(done['answer'])
                        row['refusal_or_clarification_marker']=bool(re.search(r'не наш|не раскрыва|не буду|не подтвержд|уточните|неизвест|нет подтвержд',done['answer'],re.I))
                    else:
                        row['error']='No done event'
                else:
                    history=case.get('history',[])
                    question=case['question']
                    if args.policy=='multi_query':
                        from src.core.services.query_interpretation import interpret_unit_spelling
                        question=interpret_unit_spelling(question)
                    resolved=resolve_unit_reply(question,None,history)
                    units=resolve_units(resolved,history)
                    query=contextual_query(resolved,history)
                    if units:
                        query+='\nПодразделение: '+'; '.join(u.name for u in units)
                    retriever.unit_keys=tuple(u.key for u in units)
                    result=await retriever.retrieve(query)
                    final=context_hits(restore_reviewed_sections(prepare_source_hits(result.hits,query),query))
                    def found(hits):
                        return bool(case.get('quote')) and any(h.url==case.get('url') and norm(case['quote']) in norm(h.content) for h in hits)
                    row={'id':case['id'],'split':case['split'],'question':case['question'],'answerable':case['answerable'],
                         'query':query,'status':result.status,'retrieved_evidence':found(result.hits),'prompt_evidence':found(final),
                         'diagnostics':result.diagnostics,'hits':[{'url':h.url,'chunk_id':h.chunk_id,'content':h.content,'meta':h.meta} for h in final]}
                report['rows'].append(row)
                atomic_json(destination,report)
                if len(report['rows'])%10==0:
                    print(args.database,args.policy,report['kind'],len(report['rows']),'/',len(cases),flush=True)
        async with sessions() as db:
            report['corpus_after']=await corpus_stamp(db)
        report['corpus_unchanged']=report['corpus_before']==report['corpus_after']
        report['code_after']=code_stamp()
        report['code_unchanged']=report['code_before']==report['code_after']
        summary={}
        for split in ['development','control','dialogue','guardrail']:
            rows=[r for r in report['rows'] if r['split']==split]
            if not rows:
                continue
            if args.answers:
                summary[split]={'questions':len(rows),'completed':sum(bool(r.get('response')) for r in rows),
                    'literal_answer_markers':sum(r.get('expected_answer_marker',False) for r in rows),
                    'refusal_or_clarification_markers':sum(r.get('refusal_or_clarification_marker',False) for r in rows),
                    'first_text_p95_ms':p95([r['first_text_ms'] for r in rows if r['first_text_ms'] is not None]),
                    'full_answer_p95_ms':p95([r['total_ms'] for r in rows]),
                    'search_p95_ms':p95([(r.get('response') or {}).get('usage',{}).get('search_ms',0) for r in rows])}
            else:
                positive=[r for r in rows if r['answerable']]
                summary[split]={'questions':len(rows),'positive':len(positive),
                    'retrieval_evidence':sum(r['retrieved_evidence'] for r in positive),
                    'prompt_evidence':sum(r['prompt_evidence'] for r in positive),
                    'unavailable':sum(r['status']=='unavailable' for r in rows),
                    'search_p95_ms':p95([r['diagnostics']['total_ms'] for r in rows])}
        report['summary']=summary
        atomic_json(destination,report)
        print(json.dumps(summary,ensure_ascii=False,indent=2),flush=True)
    finally:
        await engine.dispose()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database',required=True)
    parser.add_argument('--policy',choices=['profile','multi_query'],default='profile')
    parser.add_argument('--cases',default='tests/eval/deep_cases_v1.json')
    parser.add_argument('--out',required=True)
    parser.add_argument('--rerank',action='store_true')
    parser.add_argument('--answers',action='store_true')
    parser.add_argument('--answer-mode',choices=['generative','reviewed_quotes','source_quotes'])
    parser.add_argument('--limit',type=int)
    parser.add_argument('--ids')
    asyncio.run(evaluate(parser.parse_args()))


if __name__=='__main__':
    main()
