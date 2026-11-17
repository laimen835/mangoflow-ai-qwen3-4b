"""Compare two local models on identical contexts; never switch settings automatically."""
import asyncio
import json
import statistics
import sys
import time
from pathlib import Path
import httpx
from scripts.quality_cases import RAG_CASES, check_answer
from src.core.config import settings
from src.core.db.session import AsyncSessionLocal, engine
from src.core.llm.ollama_client import OllamaClient
from src.core.rag.embeddings import build_embedder
from src.core.rag.prompt import build_messages
from src.core.rag.query import contextual_query
from src.core.rag.retriever import HybridRetriever


async def main():
    contexts=[]
    async with AsyncSessionLocal() as db:
        retriever=HybridRetriever(db,build_embedder(settings),settings)
        for case in RAG_CASES:
            history=[{'role':'user','content':case['setup']}] if case.get('setup') else []
            hits=await retriever.search(contextual_query(case['question'],history))
            contexts.append((case,build_messages(case['question'],hits,history),[{'title':h.title,'url':h.url,'content':h.content}for h in hits]))
    results=[];capabilities={}
    for model in ['qwen3:4b-instruct','qwen3:8b']:
        async with httpx.AsyncClient(timeout=30) as c:
            response=await c.post(settings.ollama_base_url+'/api/show',json={'model':model})
            response.raise_for_status();capabilities[model]=response.json().get('capabilities',[])
        client=OllamaClient(settings.model_copy(update={'llm_model':model,'llm_timeout_s':240,'ollama_think':False}))
        # Warm the model with the first actual context, excluded from timings.
        await client.generate(contexts[0][1],max_tokens=20,temperature=0)
        for index,(case,messages,hits) in enumerate(contexts,1):
            started=time.monotonic();first=None;parts=[];error=None
            try:
                async for event in client.stream(messages,max_tokens=500,temperature=0.1):
                    if event.get('delta'):
                        if first is None:first=time.monotonic()-started
                        parts.append(event['delta'])
            except Exception as exc:error=str(exc)
            text=''.join(parts)
            results.append({'model':model,'case':index,'question':case['question'],'answer':text,'error':error,
                'phrase_check':not error and check_answer(text,case),
                'first_text_seconds':round(first,3) if first is not None else None,
                'total_seconds':round(time.monotonic()-started,3),'sources':hits})
            Path('logs/widget-model-comparison.json').write_text(json.dumps({'capabilities':capabilities,'results':results,
                'decision':'Pending factual review; keep qwen3:4b-instruct until accuracy and latency criteria pass.',
                'limitation':'20 targeted RAG questions, phrase checks are provisional. Identical sources, 4096 context, 500 output tokens, think=false.'},ensure_ascii=False,indent=2),encoding='utf-8')
            print(f"{model} {index}/20 {'PASS' if results[-1]['phrase_check'] else 'FAIL'} {results[-1]['total_seconds']}s",flush=True)
    summary={}
    for model in capabilities:
        rows=[r for r in results if r['model']==model]
        summary[model]={'phrase_passed':sum(r['phrase_check'] for r in rows),'cases':len(rows),
            'median_seconds':round(statistics.median(r['total_seconds'] for r in rows),3),
            'median_first_text_seconds':round(statistics.median(r['first_text_seconds'] for r in rows if r['first_text_seconds'] is not None),3)}
    report=json.loads(Path('logs/widget-model-comparison.json').read_text(encoding='utf-8'));report['summary']=summary
    Path('logs/widget-model-comparison.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(summary,ensure_ascii=False),flush=True)
    await engine.dispose()


if __name__=='__main__':
    sys.stdout.reconfigure(encoding='utf-8');asyncio.run(main())
