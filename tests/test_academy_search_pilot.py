from types import SimpleNamespace
from unittest.mock import AsyncMock,patch

import pytest
from src.core.services.academy_scope import linked_department_scope
from src.core.services.academy_scope import requested_department_matches
from src.core.rag.academy_retrieval import academy_candidates,academy_search
from src.core.rag.vector_search import SearchHit


def test_neutral_department_inherits_only_verified_unit_root_navigation():
    url='https://ncsa.ru/about_the_university/structure/chairs/2485/'
    reference={'url':url,'parent':'https://ncsa.ru/institutes/137/','title':'Астрофизика'}
    assert linked_department_scope(url,[reference])['unit_key']=='137'
    assert linked_department_scope(url,[{**reference,'parent':'https://evil.test/institutes/137/'}])['unit_key']=='academy'
    shared=linked_department_scope(url,[reference,{**reference,'parent':'https://ncsa.ru/institutes/138/'}])
    assert shared['unit_key']=='academy' and shared['scope_ambiguous']==['137','138']
    assert linked_department_scope('https://ncsa.ru/institutes/138/',[reference])['unit_key']=='138'
    assert linked_department_scope('https://ncsa.ru/sveden/grants/policy.pdf',[reference])['unit_key']=='academy'


def test_department_ranking_uses_full_name_and_does_not_match_another_department():
    assert requested_department_matches('Кто заведует кафедрой внутренних болезней медицинского института?','Кафедра «Внутренние болезни»')
    assert requested_department_matches('Сколько лабораторий у кафедры агротехнологии и инженерии в АПК?','Агротехнология и инженерия в АПК')
    assert not requested_department_matches('Кто заведует кафедрой математики?','Астрофизика')
    assert not requested_department_matches('Какие направления есть в медицинском институте?','Кафедра «Внутренние болезни»')
    assert not requested_department_matches('Кто заведует кафедрой?','Кафедра «Внутренние болезни»')


@pytest.mark.asyncio
async def test_semantic_candidate_survives_without_matching_query_words_and_scope_is_forwarded():
    hit=SearchHit(1,1,'Реализация мероприятий','Положение','https://ncsa.ru/a',1,.9,'vector')
    cfg=SimpleNamespace(top_k_vector=20,top_k_bm25=20,rrf_k=60,reranker_candidates=20)
    embedder=SimpleNamespace(embed_one=AsyncMock(return_value=[1.0]))
    with patch('src.core.rag.academy_retrieval.vector_search',new=AsyncMock(return_value=[hit])) as vector, patch('src.core.rag.academy_retrieval.bm25_search',new=AsyncMock(return_value=[])):
        hits,diag=await academy_candidates(None,embedder,cfg,'Где переночевать?',unit_keys=('137',))
    assert [h.chunk_id for h in hits]==[1]
    assert vector.call_args.kwargs['unit_keys']==('137',)


@pytest.mark.asyncio
async def test_missing_reranker_reports_fallback_without_fake_scores():
    hit=SearchHit(1,1,'Подтвержденный текст','Документ','https://ncsa.ru/a',1,.9,'vector')
    cfg=SimpleNamespace(academy_reranker_directory='missing')
    with patch('src.core.rag.academy_retrieval.academy_candidates',new=AsyncMock(return_value=([hit],{'timings_ms':{}}))), patch('src.core.rag.academy_retrieval.rerank',new=AsyncMock(side_effect=FileNotFoundError)):
        hits,diag=await academy_search(None,None,cfg,'Вопрос')
    assert hits==[hit] and hit.rerank_score is None
    assert diag['reranker']=='unavailable' and diag['reranker_error']=='FileNotFoundError'


@pytest.mark.asyncio
async def test_named_department_is_scoped_before_topk_and_fact_words_drive_lexical_search():
    from src.core.rag.legacy_retriever import HybridRetriever
    hit=SearchHit(7,42,'Кафедра внутренних болезней. Кабинет 1.15.','Кафедра','https://ncsa.ru/a',1,.9,'vector')
    cfg=SimpleNamespace(academy_department_navigation=True,top_k_final=4,top_k_vector=20,top_k_bm25=20,rrf_k=60)
    db=SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(all=lambda:[(42,{'department_name':'Кафедра «Внутренние болезни»','unit_name':'Медицинский институт'})])))
    embedder=SimpleNamespace(embed_one=AsyncMock(return_value=[1.0]))
    with patch('src.core.rag.legacy_retriever.vector_search',new=AsyncMock(return_value=[hit])) as vector, patch('src.core.rag.legacy_retriever.bm25_search',new=AsyncMock(return_value=[hit])) as lexical:
        await HybridRetriever(db,embedder,cfg,unit_keys=('150',)).search('В каком кабинете находится кафедра внутренних болезней медицинского института?')
    assert vector.call_args.kwargs['document_ids']==(42,)
    assert vector.call_args.kwargs['unit_keys']==('150',)
    assert lexical.call_args_list[0].kwargs['document_ids']==(42,)
    assert 'кабинет' in lexical.call_args_list[0].kwargs['query']
    assert 'внутренний' not in lexical.call_args_list[0].kwargs['query']


@pytest.mark.asyncio
async def test_scoped_followup_keeps_previous_admission_topic(db,llm,settings):
    from src.core.api.schemas import ChatRequest
    from src.core.services.chat_service import ChatService
    class RecordingRetriever:
        def __init__(self):self.queries=[]
        async def search(self,query,**kwargs):
            self.queries.append(query)
            return []
    retriever=RecordingRetriever()
    service=ChatService(db,llm,retriever,settings)
    first=await service.handle(ChatRequest(message='Где найти информацию о поступлении в СПК?'))
    await service.handle(ChatRequest(message='А какие документы нужны?',session_id=first.session_id))
    assert 'поступлен' in retriever.queries[-1]
    assert 'какие документы' in retriever.queries[-1].casefold()
    assert 'Подразделение:' in retriever.queries[-1]
