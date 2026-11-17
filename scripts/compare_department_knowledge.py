"""Isolate seven new department pages from the reranker experiment."""
import asyncio
import argparse
import json
import time
from pathlib import Path

from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine,async_sessionmaker
from src.core.api.schemas import ChatRequest
from src.core.config import settings
from src.core.llm import build_llm
from src.core.rag.embeddings import build_embedder
from src.core.rag.retriever import HybridRetriever
from src.core.services.academy_scope import resolve_units
from src.core.services.chat_service import ChatService

CASES=[
    ('Кто заведует кафедрой астрофизики института цифровых технологий?','/2485/','Валявин Геннадий'),
    ('Сколько преподавателей в штате кафедры государственных и административно-правовых дисциплин юридического института?','/2505/','10 преподавателей'),
    ('Сколько лабораторий у кафедры агротехнологии и инженерии в АПК аграрного института?','/3739/','7 лабораторий'),
    ('Кто заведует кафедрой дизайна и изобразительного искусства института дизайна и лингвистики?','/164/','Хубиева Зарима Юсуфовна'),
    ('Когда образована кафедра мехатронных и робототехнических систем инженерного института?','/2477/','образована в 2025 году'),
    ('Какой режим работы кафедры бухгалтерского учета института экономики и управления?','/309/','8:00-17:00'),
    ('В каком кабинете находится кафедра внутренних болезней медицинского института?','/2487/','каб. 1.15'),
]


async def main(navigation=False):
    record=json.loads(Path('D:/Codex-RAG-runtime/academy-site-current.json').read_text());folder=Path(record['folder'])
    materials=json.loads((folder/'department-materials.json').read_text())
    report={'limitation':'Seven source-selected department facts; useful pilot, not independent full-site accuracy. Same legacy search and Qwen in both modes. BGE disabled.','modes':{}}
    destination=folder/('department-navigation-comparison.json' if navigation else 'department-comparison.json')
    modes=(('after_navigation',record['candidate_database']),) if navigation else (('before',record['baseline_database']),('after',record['candidate_database']))
    for mode,dbname in modes:
        cfg=settings.model_copy(update={'database_url':make_url(settings.database_url).set(database=dbname).render_as_string(hide_password=False),
                                       'academy_rerank_enabled':False,'academy_department_navigation':navigation})
        engine=create_async_engine(cfg.database_url);sessions=async_sessionmaker(engine,expire_on_commit=False)
        result={'retrieval':[],'chat':[]};report['modes'][mode]=result
        try:
            async with sessions() as db:
                retriever=HybridRetriever(db,build_embedder(cfg),cfg)
                for question,suffix,quote in CASES:
                    source=next(s for s in materials if s['url'].endswith(suffix))
                    assert quote in ' '.join(source['text'].split())
                    retriever.unit_keys=tuple(u.key for u in resolve_units(question))
                    hits=await retriever.search(question,top_k=4)
                    row={'question':question,'official_url':source['url'],'verified_quote':quote,
                         'new_source_found':any(h.url==source['url'] for h in hits),
                         'quote_found':any(quote in ' '.join(h.content.split()) for h in hits),
                         'hits':[{'id':h.chunk_id,'url':h.url,'content':h.content} for h in hits]}
                    result['retrieval'].append(row)
                for question in [CASES[i][0] for i in (0,2,4,6)]+['Кто будет директором института цифровых технологий в 2035 году?']:
                    service=ChatService(db,build_llm(cfg),HybridRetriever(db,build_embedder(cfg),cfg),cfg)
                    started=time.perf_counter()
                    answer=await service.handle(ChatRequest(message=question))
                    result['chat'].append({'question':question,'answer':answer.answer,'sources':[s.model_dump(mode='json') for s in answer.sources],
                                           'usage':answer.usage,'seconds':round(time.perf_counter()-started,3)})
                    destination.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
                    print(mode,question,answer.answer[:130].replace('\n',' '),flush=True)
        finally:
            await engine.dispose()
        destination.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        print(mode,'quoted evidence in top4',sum(r['quote_found'] for r in result['retrieval']),'/7',flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--navigation',action='store_true');args=parser.parse_args()
    asyncio.run(main(args.navigation))
