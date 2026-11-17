"""Measure a private SSH-forwarded copy; never publish or edit the corpus."""
import argparse
import asyncio
import json
import time
from pathlib import Path
from urllib.parse import urlsplit
import httpx

QUESTIONS = [
    'Какие направления подготовки есть в институте цифровых технологий?',
    'Какие документы нужны для поступления в СПК?',
    'Как оформить материальную помощь в СПК?',
    'Кто директор института цифровых технологий?',
    'Кто был директором института цифровых технологий в 1990 году?',
]


async def benchmark(base, destination):
    url = urlsplit(base)
    if url.hostname not in {'localhost','127.0.0.1','::1'} or url.scheme not in {'http','https'}:
        raise ValueError('Use a loopback SSH-forwarded endpoint')
    rows = []
    async with httpx.AsyncClient(timeout=600, trust_env=False) as client:
        response = await client.get(base.rstrip('/')+'/ready')
        response.raise_for_status()
        ready = response.json()
        if not (ready.get('db') and ready.get('llm')):
            raise RuntimeError('Imported database and exact models are not ready')
        # Repeat the first generative question after warm-up. Use fresh sessions
        # so an earlier turn cannot supply facts or change the unit under test.
        for index, question in enumerate([*QUESTIONS, QUESTIONS[0]]):
            started = time.perf_counter()
            first = None
            final = None
            async with client.stream('POST',base.rstrip('/')+'/api/v1/chat/stream',json={'message':question}) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line.strip():
                        continue
                    event = json.loads(line)
                    if event['type']=='delta' and event.get('text') and first is None:
                        first = round(time.perf_counter()-started,3)
                    if event['type']=='error':
                        raise RuntimeError(event.get('message','Stream failed'))
                    if event['type']=='done':
                        final = event['data']
            if final is None:
                raise RuntimeError('Stream ended without a final answer')
            rows.append({'question':question,'cold_first_question':index==0,
                'first_text_s':first,'total_s':round(time.perf_counter()-started,3),
                'answer':final['answer'],'sources':final.get('sources',[]),'usage':final.get('usage',{}),
                'content_quality':'requires manual source review'})
            destination.write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding='utf-8')
            print(json.dumps({'question':question,'first_text_s':first,'total_s':rows[-1]['total_s'],
                              'model':final.get('usage',{}).get('model')},ensure_ascii=False),flush=True)


if __name__=='__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--base-url',default='http://127.0.0.1:8003')
    parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args()
    asyncio.run(benchmark(args.base_url,args.output))
