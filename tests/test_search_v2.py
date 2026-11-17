from types import SimpleNamespace
import pytest
from src.core.rag.retriever import HybridRetriever
from src.core.rag.vector_search import SearchHit
from src.core.rag.profiles import prepare,fingerprint
from src.core.rag.query import contextual_query
from src.core.rag.prompt import context_hits,build_context


@pytest.fixture
def semantic(monkeypatch,settings):
    profile={'name':'candidate','model':settings.embedding_model,'dimension':768,
             'query_prefix':'search_query: ','document_prefix':'search_document: ',
             'normalize':True,'version':2,'policy':'semantic','rerank_threshold':0.0}
    async def state(*a,**kw):return SimpleNamespace(active_profile=profile,candidate_profile=profile)
    monkeypatch.setattr('src.core.rag.retriever.state_for',state)
    class Embedder:
        async def embed_one(self,q):
            assert q.startswith('search_query: ')
            return [0.0]
    return HybridRetriever(None,Embedder(),settings)


def hit(i,text,score=0.95):return SearchHit(i,i,text,'Материал','https://ncsa.ru/',1,score,'vector')


@pytest.mark.asyncio
async def test_semantic_match_does_not_require_shared_words(monkeypatch,semantic):
    async def vector(*a,**kw):return [hit(1,'Предоставляется место в общежитии.')]
    async def lexical(*a,**kw):return []
    async def rank(*a,**kw):return [2.0]
    monkeypatch.setattr('src.core.rag.retriever.vector_search',vector)
    monkeypatch.setattr('src.core.rag.retriever.bm25_search',lexical)
    monkeypatch.setattr('src.core.rag.retriever.rerank',rank)
    result=await semantic.retrieve('Ищу ночлег поблизости от учёбы')
    assert result.status=='matched' and result.hits[0].chunk_id==1
    assert result.usage()['reranker_status']=='ready'


@pytest.mark.asyncio
async def test_reranker_failure_degrades_to_hybrid(monkeypatch,semantic):
    async def vector(*a,**kw):return [hit(1,'Данные общежития.')]
    async def lexical(*a,**kw):return []
    async def broken(*a,**kw):raise FileNotFoundError('missing local model')
    monkeypatch.setattr('src.core.rag.retriever.vector_search',vector)
    monkeypatch.setattr('src.core.rag.retriever.bm25_search',lexical)
    monkeypatch.setattr('src.core.rag.retriever.rerank',broken)
    result=await semantic.retrieve('Где жить?')
    assert result.hits and result.diagnostics['reranker']=='unavailable'


@pytest.mark.asyncio
async def test_infrastructure_failure_is_not_no_information(monkeypatch,semantic):
    async def broken(*a,**kw):raise RuntimeError('offline')
    monkeypatch.setattr('src.core.rag.retriever.vector_search',broken)
    monkeypatch.setattr('src.core.rag.retriever.bm25_search',broken)
    result=await semantic.retrieve('Колледж')
    assert result.status=='unavailable' and not result.hits


@pytest.mark.asyncio
async def test_candidate_cap_and_no_low_score_sources(monkeypatch,semantic):
    async def vector(*a,**kw):return [hit(i,f'Различный материал номер {i}') for i in range(1,31)]
    async def lexical(*a,**kw):return []
    async def rank(query,texts,path):
        assert len(texts)<=20
        return [2.0,1.0]+[-9.0]*(len(texts)-2)
    monkeypatch.setattr('src.core.rag.retriever.vector_search',vector)
    monkeypatch.setattr('src.core.rag.retriever.bm25_search',lexical)
    monkeypatch.setattr('src.core.rag.retriever.rerank',rank)
    result=await semantic.retrieve('Материалы',top_k=50)
    assert len(result.hits)==2
    assert any(c['reason']=='below_threshold' for c in result.diagnostics['candidates'])


def test_profile_formatting_and_fingerprints():
    p={'query_prefix':'search_query: ','document_prefix':'search_document: ','normalize':True,'version':2}
    assert prepare('текст\n  запроса',p,'query')=='search_query: текст запроса'
    assert prepare('Документ',p,'document')=='search_document: Документ'
    assert fingerprint(p)!=fingerprint({**p,'version':3})
    assert fingerprint(p)==fingerprint({**p,'rerank_threshold':-5})


def test_identifier_followups_replace_previous_identifier():
    h=[{'role':'user','content':'Какая квалификация у 09.02.07?'}]
    q=contextual_query('А у 09.02.12?',h)
    assert 'квалификация' in q and '09.02.12' in q and '09.02.07' not in q
    h=[{'role':'user','content':'Какие сроки поступления в 2026 году?'}]
    q=contextual_query('А в 2027?',h)
    assert 'сроки' in q and '2027' in q and '2026' not in q


def test_context_sources_match_exactly_what_was_included():
    hits=[hit(1,'a'*100),hit(2,'b'*100),hit(3,'c'*100)]
    chosen=context_hits(hits,max_chars=180)
    assert len(chosen)==1
    context=build_context(chosen,max_chars=180)
    assert '[1]' in context and '[2]' not in context


def test_explicit_topic_change_is_not_replaced_by_previous_year_or_code():
    h=[{'role':'user','content':'Сколько стоит обучение в 2026 году по 09.02.07?'}]
    for question in ['А какие документы нужны для поступления в 2027?', 'А какая квалификация у 09.02.12?']:
        assert contextual_query(question,h)==question


def test_settings_repr_hides_credentials(settings):
    s=settings.model_copy(update={'database_url':'private-dsn','admin_api_key':'private-admin','openai_api_key':'private-key'})
    assert 'private-' not in repr(s)
