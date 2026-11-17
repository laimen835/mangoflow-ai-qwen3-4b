from io import BytesIO
import json
import zipfile

import httpx
import pytest

from src.core.rag.crawl_policy import CrawlConfig
from src.core.rag.deep_crawler import DeepCrawler, FetchFailure, Manifest, PoliteFetcher, discover_sitemaps
from src.core.rag.knowledge_extract import html_content, file_content, full_text, pptx_blocks, semantic_chunks
from src.core.rag.structured_documents import Block
from src.core.rag.multi_query import query_plan


@pytest.mark.parametrize('url',[
    'https://ncsa.ru.evil.org/institutes/', 'https://ncsa.ru@evil.org/',
    'https://ncsa.ru:444/', 'http://127.0.0.1/', 'file:///etc/passwd',
    '/%2561dmin/', '/x/../admin/', '/auth/login.php', '/login.php',
    '/x?logout=yes', '/x?PAGEN_1=1001', '/x?PAGEN_1=-1', '/x?PAGEN_1=abc',
    '/x\\admin', '/%252e%252e/other', '/%00admin/',
    'ict@ncsa.ru', 'https://ncsa.ru/institutes/137/ict@ncsa.ru',
])
def test_crawl_boundary_rejects_unsafe_resources(url):
    assert CrawlConfig().canonical(url) is None


def test_pagination_identifiers_and_tracking():
    policy=CrawlConfig()
    first=policy.canonical('/archive/?PAGEN_1=1&ELEMENT_ID=137&utm_source=mail#tab')
    second=policy.canonical('/archive/?PAGEN_1=2&ELEMENT_ID=137')
    assert first=='https://ncsa.ru/archive/?ELEMENT_ID=137&PAGEN_1=1'
    assert first!=second
    assert policy.canonical('/a b.docx')=='https://ncsa.ru/a%20b.docx'
    assert policy.canonical('http://www.ncsa.ru/index.php')=='https://ncsa.ru/'


def test_full_text_sections_and_table_context():
    body='<title>Страница</title><main><h1>Институт</h1><p>Начало.</p><h2>Образование</h2><p>Средняя часть.</p><table><tr><th>Код</th><th>Срок</th></tr><tr><td>09.03.04</td><td>4 года</td></tr></table><div>Окончание.</div><a href="/files/a.docx">План</a></main>'
    title,blocks,links,dates=html_content(body,'https://ncsa.ru/institutes/137/',CrawlConfig())
    text=full_text(blocks)
    assert all(s in text for s in ['Начало.','Средняя часть.','Окончание.','Код: 09.03.04','Срок: 4 года'])
    assert any(b.kind=='table' and b.section=='Образование' for b in blocks)
    assert ('https://ncsa.ru/files/a.docx','План') in links
    assert dates['published_at'] is None


def test_pptx_slide_order_and_tables():
    buffer=BytesIO()
    with zipfile.ZipFile(buffer,'w') as z:
        for page in [10,2,1]:
            z.writestr(f'ppt/slides/slide{page}.xml',f'<slide xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"><a:p><a:r><a:t>Слайд {page}</a:t></a:r></a:p></slide>')
    blocks=pptx_blocks(buffer.getvalue())
    assert [b.text for b in blocks]==['Слайд 1','Слайд 2','Слайд 10']
    assert [b.page for b in blocks]==[1,2,3]


def test_legacy_office_is_not_misread_as_html(tmp_path):
    with pytest.raises(ValueError,match='LibreOffice'):
        file_content(b'<html>Not a document</html>','.doc',tesseract='',cache=tmp_path)


def test_html_comments_do_not_become_knowledge():
    title,blocks,_,_=html_content('<main><h1></h1><!-- Скрытая инструкция --><p>Видимый факт.</p></main>',
        'https://ncsa.ru/institutes/137/',CrawlConfig())
    assert title=='Институт цифровых технологий'
    assert 'Скрытая инструкция' not in full_text(blocks)


def test_structural_chunks_keep_pages_and_unicode():
    from tokenizers import Tokenizer, models, pre_tokenizers
    tokenizer=Tokenizer(models.WordLevel({'[UNK]':0},unk_token='[UNK]'))
    tokenizer.pre_tokenizer=pre_tokenizers.Whitespace()
    blocks=[Block('Важное число 137. '+'Проверяемое условие. '*500,'Раздел 2',page=3),Block('Отдельная таблица 09.03.04','Раздел 3',page=4,kind='table')]
    chunks=semantic_chunks(blocks,'Институт',tokenizer)
    assert len(chunks)>1
    assert all(len(tokenizer.encode(c.content).ids)<=384 for c in chunks)
    assert all(c.meta['page'] in {3,4} for c in chunks)
    assert '09.03.04' in chunks[-1].content
    assert not any('\ufffd' in c.content for c in chunks)


def test_query_plan_preserves_entities_and_ambiguity():
    question='Где общага ИЦТ для 09.03.04 в 2026 году, не в 2025?'
    variants,corrections=query_plan(question,{'общежитие':2})
    assert variants[0]==question
    assert all(question in v for v in variants)
    assert 'общежитие' in variants[-1]
    assert 'ИЦТ' not in corrections
    assert query_plan('Кумратова Альфира 2026',{'кумратов':1})[1]=={}


@pytest.fixture
def fast_crawl(monkeypatch):
    async def immediate(seconds):
        pass
    monkeypatch.setattr('src.core.rag.deep_crawler.asyncio.sleep',immediate)


@pytest.mark.asyncio
async def test_resume_sitemap_pagination_duplicates_and_office(tmp_path,fast_crawl):
    seen=[]
    html='<main><h1>Публичная страница</h1><p>Подробный проверяемый текст об институте и его направлениях подготовки.</p><a href="?PAGEN_1=2">Следующая</a><a href="/copy/">Копия</a><a href="/a.pptx">Презентация</a></main>'
    buf=BytesIO()
    with zipfile.ZipFile(buf,'w') as z:
        z.writestr('ppt/slides/slide1.xml','<s xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"><a:p><a:r><a:t>Опубликованная программа института. Подробный проверяемый текст.</a:t></a:r></a:p></s>')
    def handle(request):
        seen.append(str(request.url))
        path=request.url.path
        if path=='/robots.txt':
            return httpx.Response(200,text='User-agent: *\nDisallow: /private/\nSitemap: https://ncsa.ru/sitemap.xml')
        if path=='/sitemap.xml':
            return httpx.Response(200,text='<urlset><url><loc>https://ncsa.ru/private/</loc></url></urlset>')
        if path=='/a.pptx':
            return httpx.Response(200,content=buf.getvalue())
        return httpx.Response(200,text=html,headers={'content-type':'text/html','etag':'v1'})
    config=CrawlConfig(max_pages=2,max_documents=1)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        report=await DeepCrawler(config,tmp_path,client=client).run(validate_dns=False)
        assert report['remaining']>0
        report2=await DeepCrawler(config,tmp_path,client=client).run(validate_dns=False)
    assert 'https://ncsa.ru/private/' not in seen
    assert report2['downloaded_documents']==1
    assert report2['duplicates']>=1
    assert any('PAGEN_1=2' in u for u in seen)
    assert any(d['state']=='denied' for d in report2['documents'])
    assert all(__import__('pathlib').Path(d['text_file']).exists() for d in report2['documents'] if 'text_file' in d)


@pytest.mark.asyncio
@pytest.mark.parametrize('status',[401,403,500,503])
async def test_robots_failure_never_means_permission(tmp_path,fast_crawl,status):
    seen=[]
    def handle(request):
        seen.append(str(request.url))
        return httpx.Response(status)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        report=await DeepCrawler(CrawlConfig(),tmp_path,client=client).run(validate_dns=False)
    assert report.get('blocked') is True
    assert seen==['https://ncsa.ru/robots.txt']


@pytest.mark.asyncio
async def test_redirect_to_another_origin_is_not_fetched(tmp_path,fast_crawl):
    seen=[]
    def handle(request):
        seen.append(str(request.url))
        return httpx.Response(302,headers={'location':'http://127.0.0.1/private'})
    config=CrawlConfig()
    manifest=Manifest(tmp_path/'manifest.sqlite3',config)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        fetcher=PoliteFetcher(client,config,manifest)
        with pytest.raises(FetchFailure,match='unsafe_redirect'):
            await fetcher.fetch(config.seeds[0],robots=False)
    manifest.db.close()
    assert seen==list(config.seeds)


def test_manifest_cannot_resume_another_organization_or_database(tmp_path):
    path=tmp_path/'manifest.sqlite3'
    Manifest(path,CrawlConfig(),'db-one').db.close()
    with pytest.raises(ValueError,match='another crawl'):
        Manifest(path,CrawlConfig(),'db-two')
    with pytest.raises(ValueError,match='another crawl'):
        Manifest(path,CrawlConfig(organization='another'),'db-one')


@pytest.mark.asyncio
async def test_304_uses_original_then_refreshes_text(tmp_path,fast_crawl):
    requests=[]
    html='<main><h1>Институт</h1><p>Полное публичное содержание страницы института для воспроизводимой индексации.</p></main>'
    def handle(request):
        requests.append(request)
        if request.url.path in {'/robots.txt','/sitemap.xml','/sitemap_index.xml'}:
            return httpx.Response(404)
        if request.headers.get('if-none-match')=='v1':
            return httpx.Response(304)
        return httpx.Response(200,text=html,headers={'etag':'v1','content-type':'text/html'})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        crawler=DeepCrawler(CrawlConfig(),tmp_path,client=client)
        first=await crawler.run(validate_dns=False)
        second=await crawler.run(validate_dns=False,refresh=True)
    assert first['downloaded_resources']==second['downloaded_resources']==1
    assert second['documents'][0]['http_status']==304
    assert second['documents'][0]['text_sha256']==first['documents'][0]['text_sha256']


@pytest.mark.asyncio
async def test_quarantine_cannot_be_cleared_by_new_importer(db,settings,tmp_path):
    from src.core.db.models import Document,DocumentStatus
    doc=Document(title='Непроверенный материал',source_type='ncsa_pdf',external_id='https://ncsa.ru/a.pdf',
        status=DocumentStatus.INDEXED,meta={'review_required':True})
    db.add(doc)
    await db.flush()
    crawler=DeepCrawler(CrawlConfig(),tmp_path,db=db,settings=settings)
    result=await crawler.ingest({'url':'https://ncsa.ru/a.pdf','suffix':'.pdf'},'Новый заголовок',[Block('Текст')],{},tmp_path/'unused.pdf')
    assert result['state']=='review_conflict'
    assert doc.meta['review_required'] is True


def test_program_identity_is_repeated_in_semantic_sections():
    html='<main><h1>Институт</h1><div>Образовательная программа 09.03.04 Программная инженерия</div><p>Базы данных.</p><p>Операционные системы.</p><div>Образовательная программа 09.04.03 Прикладная информатика</div><p>Управление проектами.</p></main>'
    _,blocks,_,_=html_content(html,'https://ncsa.ru/institutes/137/',CrawlConfig())
    assert next(b for b in blocks if 'Операционные системы' in b.text).section.startswith('Образовательная программа 09.03.04')
    assert next(b for b in blocks if 'Управление проектами' in b.text).section.startswith('Образовательная программа 09.04.03')


def test_mixed_pdf_ocr_keeps_native_page_and_exact_page_numbers(tmp_path,monkeypatch):
    import pymupdf
    pdf=pymupdf.open()
    page=pdf.new_page()
    page.insert_text((40,50),'Native source text remains authoritative. '*4)
    pdf.new_page()
    body=pdf.tobytes()
    pdf.close()
    monkeypatch.setattr('src.core.rag.ocr.recognize_pdf',lambda *args,**kwargs:['Do not replace native text','Распознанный текст второй страницы.'])
    blocks,meta=file_content(body,'.pdf',tesseract='',cache=tmp_path)
    assert meta['ocr'] is True
    assert 'Native source text' in next(b.text for b in blocks if b.page==1)
    assert next(b.text for b in blocks if b.page==2)=='Распознанный текст второй страницы.'


def test_unreadable_pdf_page_is_reported_instead_of_omitted(tmp_path,monkeypatch):
    import pymupdf
    pdf=pymupdf.open()
    pdf.new_page()
    body=pdf.tobytes()
    pdf.close()
    monkeypatch.setattr('src.core.rag.ocr.recognize_pdf',lambda *args,**kwargs:[''])
    with pytest.raises(ValueError,match='Unreadable PDF pages: 1'):
        file_content(body,'.pdf',tesseract='',cache=tmp_path)


@pytest.mark.asyncio
async def test_multi_query_enforces_scope_quarantine_and_period_before_top_k(db,settings):
    from src.core.db.models import Document,DocumentStatus,Chunk
    from src.core.rag.multi_query import multi_query_search
    from src.core.rag.profiles import state_for
    records=[]
    for key,meta in [('wanted',{'unit_key':'137'}),('other',{'unit_key':'138'}),
                     ('quarantine',{'unit_key':'137','review_required':True}),
                     ('old_period',{'unit_key':'137','review':{'scope':'spo_confirmed','text_verified':True,
                        'period_basis':'academic_cycle','period':'2020/2021 учебный год'}})]:
        doc=Document(title='Уникальный пример для поиска',source_type='deep_fixture',external_id='deep-'+key,
            source_url='https://ncsa.ru/source/'+key,status=DocumentStatus.INDEXED,meta=meta)
        db.add(doc)
        await db.flush()
        db.add(Chunk(document_id=doc.id,chunk_index=0,content='Собрезультат: опубликованная работа кафедры, 2026.',embedding=[1.0]*settings.embedding_dim,meta={}))
        records.append(doc)
    await db.flush()
    seen=[]
    class Embedder:
        async def embed_one(self,text):
            seen.append(text)
            return [1.0]*settings.embedding_dim
    state=await state_for(db,settings)
    hits,diag=await multi_query_search(db,Embedder(),settings,'Собрезультат кафедры 2026',state.active_profile,unit_keys=('137',))
    ids={h.document_id for h in hits}
    assert records[0].id in ids
    assert not ids & {d.id for d in records[1:]}
    assert seen[0]==state.active_profile.get('query_prefix','')+'Собрезультат кафедры 2026'


@pytest.mark.asyncio
async def test_multi_query_database_failure_is_unavailable_not_missing(monkeypatch,settings):
    from types import SimpleNamespace
    from src.core.rag.retriever import HybridRetriever
    async def state(*args,**kwargs):
        return SimpleNamespace(active_profile={'name':'legacy-v1','model':settings.embedding_model,'dimension':settings.embedding_dim})
    async def broken(*args,**kwargs):
        raise RuntimeError('Database unavailable')
    monkeypatch.setattr('src.core.rag.retriever.state_for',state)
    monkeypatch.setattr('src.core.rag.multi_query.lexical_catalog',broken)
    cfg=settings.model_copy(update={'retrieval_policy':'multi_query'})
    result=await HybridRetriever(None,None,cfg).retrieve('Нужен факт')
    assert result.status=='unavailable'
    assert result.hits==[]


@pytest.mark.parametrize('question',[
    'кто директор инстетута цыфровых технологий в 2026 году?',
    'какие программы инстетута цифровых технологий 09.03.04?',
    'какие программы юридического инстетута 40.03.01?',
    'какие программы медицинского инстетута 31.05.01?',
    'история инстетута дизайна и лингвистики 2024?',
])
def test_unit_spelling_keeps_user_qualifiers(question):
    from src.core.services.query_interpretation import interpret_unit_spelling
    interpreted=interpret_unit_spelling(question)
    assert interpreted.startswith(question+'\n')
    from src.core.services.academy_scope import UNITS
    assert any(interpreted.endswith(u.name) for u in UNITS)
    assert re_numbers(interpreted)==re_numbers(question)


def re_numbers(value):
    import re
    return re.findall(r'\d+(?:\.\d+)*',value)


@pytest.mark.parametrize('question',[
    'Какая специальность Дизайн есть в СПК?',
    'Программная инженерия: что изучать?',
    'Кто директор неизвестного института?',
    'Какая профессия связана с цифровыми технологиями?',
])
def test_unit_interpretation_does_not_invent_scope(question):
    from src.core.services.query_interpretation import interpret_unit_spelling
    assert interpret_unit_spelling(question)==question


@pytest.mark.asyncio
async def test_embedding_checkpoint_resumes_batches_and_binds_profile(tmp_path):
    from src.core.rag.embedding_cache import CheckpointEmbeddings
    calls=[]
    class Provider:
        dim=2
        name='fake'
        fail=True
        async def embed_many(self,texts):
            calls.append(list(texts))
            if self.fail and len(calls)==2:
                raise RuntimeError('Interrupted embedding runner')
            return [[float(len(t)),1.0] for t in texts]
    provider=Provider()
    texts=[f'original exact source {i}' for i in range(40)]
    path=tmp_path/'embeddings.sqlite3'
    cache=CheckpointEmbeddings(provider,path,{'digest':'model-one','profile':'legacy'})
    with pytest.raises(RuntimeError,match='Interrupted'):
        await cache.embed_many(texts)
    cache.close()
    provider.fail=False
    resumed=CheckpointEmbeddings(provider,path,{'digest':'model-one','profile':'legacy'})
    vectors=await resumed.embed_many(texts)
    resumed.close()
    assert len(vectors)==40
    assert len(calls[-1])==8
    assert len(calls)==3
    changed=CheckpointEmbeddings(provider,path,{'digest':'model-two','profile':'legacy'})
    await changed.embed_many(texts[:1])
    changed.close()
    assert len(calls)==4


@pytest.mark.asyncio
async def test_nonfinite_embeddings_are_not_checkpointed(tmp_path):
    from src.core.rag.embedding_cache import CheckpointEmbeddings
    class Provider:
        dim=2
        name='fake'
        async def embed_many(self,texts):
            return [[float('nan'),1.0] for text in texts]
    cache=CheckpointEmbeddings(Provider(),tmp_path/'vectors.sqlite3',{'digest':'fixed'})
    with pytest.raises(ValueError,match='Invalid cached embedding'):
        await cache.embed_many(['source'])
    assert cache.db.execute('SELECT count(*) FROM vectors').fetchone()[0]==0
    cache.close()


@pytest.mark.asyncio
async def test_another_organization_cannot_index_into_existing_database(db):
    from src.core.rag.deep_crawler import validate_knowledge_binding
    with pytest.raises(ValueError,match='Legacy data|separate database'):
        await validate_knowledge_binding(db,CrawlConfig(organization='other'))
    with pytest.raises(ValueError,match='Legacy data|separate database'):
        await validate_knowledge_binding(db,CrawlConfig(knowledge_base='another-kb'))
    await validate_knowledge_binding(db,CrawlConfig())


def test_contact_cards_keep_role_with_correct_person():
    html='<main><h1>Институт</h1><h2>Руководство</h2><div class="contact-card"><p>Заместитель по учебной работе</p><h4><a href="/person/1/">Первый Иван Иванович</a></h4><p>Email: first@example.org</p></div><div class="contact-card"><p>Заместитель по воспитательной работе</p><h4><a href="/person/2/">Второй Петр Петрович</a></h4><p>Email: second@example.org</p></div></main>'
    _,blocks,_,_=html_content(html,'https://ncsa.ru/institutes/137/',CrawlConfig())
    cards=[b for b in blocks if b.kind=='contact']
    assert len(cards)==2
    assert 'Первый Иван' in cards[0].text and 'учебной работе' in cards[0].text
    assert 'воспитательной' not in cards[0].text
    assert 'Второй Петр' in cards[1].text and 'воспитательной работе' in cards[1].text
    assert 'https://' not in cards[0].text


def test_standalone_navigation_headings_do_not_take_retrieval_slots():
    from tokenizers import Tokenizer,models,pre_tokenizers
    tokenizer=Tokenizer(models.WordLevel({'[UNK]':0},unk_token='[UNK]'))
    tokenizer.pre_tokenizer=pre_tokenizers.Whitespace()
    blocks=[Block('Контакты','Контакты',kind='heading'),Block('Руководство','Руководство',kind='heading'),
        Block('Содержательное указание телефона 12345.','Руководство'),
        Block('Подготовлено 1500 выпускников','Подготовлено 1500 выпускников',kind='heading')]
    chunks=semantic_chunks(blocks,'Институт',tokenizer)
    assert len(chunks)==2
    assert '12345' in chunks[0].content
    assert '1500' in chunks[1].content


@pytest.mark.asyncio
async def test_general_source_review_requires_full_bound_proof_and_keeps_spo_separate(db):
    from copy import deepcopy
    from sqlalchemy import select
    from src.core.db.models import Document,DocumentStatus
    from src.core.rag.document_policy import real_document_filter,exclusion_reasons
    base={'review_policy':'source_text_v1','review_required':True,'unit_key':'137','raw_sha256':'a'*64,
          'extraction_version':'extract-fixed','source_review':{'coverage':'full_document','text_verified':True,
            'applicability_verified':True,'unit_key':'137','raw_sha256':'a'*64,'text_sha256':'b'*64,
            'extraction_version':'extract-fixed'}}
    docs=[]
    for variant in ['valid','partial','changed_raw','changed_text','changed_extraction','wrong_unit','spo_only']:
        meta=deepcopy(base)
        if variant=='partial':meta['source_review']['coverage']='pages_1_to_3'
        if variant=='changed_raw':meta['raw_sha256']='c'*64
        if variant=='changed_text':meta['source_review']['text_sha256']='d'*64
        if variant=='changed_extraction':meta['extraction_version']='new-extraction'
        if variant=='wrong_unit':meta['source_review']['unit_key']='138'
        if variant=='spo_only':meta.pop('source_review');meta['review']={'scope':'spo_confirmed','text_verified':True}
        doc=Document(title='Публичный материал '+variant,source_type='general_review_fixture',external_id=variant,
                     status=DocumentStatus.INDEXED,content_hash='b'*64,meta=meta)
        db.add(doc)
        docs.append(doc)
    await db.flush()
    eligible=(await db.scalars(select(Document.id).where(Document.id.in_([d.id for d in docs]),real_document_filter()))).all()
    assert eligible==[docs[0].id]
    assert exclusion_reasons(docs[0])==[]
    assert all(exclusion_reasons(d) for d in docs[1:])
    assert not any('СПО' in reason for d in docs[1:-1] for reason in exclusion_reasons(d))


def test_fact_query_removes_only_named_unit_and_preserves_entities():
    from src.core.rag.multi_query import scoped_fact_query,requested_source_format
    original='Как по PDF института цифровых технологий найти пункт 4.3 за 2026 год, не за 2025?'
    fact=scoped_fact_query(original,('137',))
    assert 'института цифровых технологий' not in fact
    assert 'не за 2025' in fact
    assert re_numbers(fact)==re_numbers(original)
    assert requested_source_format(original)=='pdf'
    assert requested_source_format('Где скачать PDF института?') is None


@pytest.mark.asyncio
async def test_pdf_query_cannot_use_html_link_catalog_as_document_evidence(db,settings):
    from src.core.db.models import Document,DocumentStatus,Chunk
    from src.core.rag.multi_query import lexical_catalog
    for kind,url in [('html','https://ncsa.ru/catalog-fixture/'),('pdf','https://ncsa.ru/actual-fixture.pdf')]:
        doc=Document(title='Собеседование PDF',source_type='format_fixture',external_id='format-'+kind,
            source_url=url,status=DocumentStatus.INDEXED,meta={'unit_key':'137','document_type':kind})
        db.add(doc)
        await db.flush()
        db.add(Chunk(document_id=doc.id,chunk_index=0,content='Собеседование, друзья, PDF.',meta={}))
    await db.flush()
    data=await lexical_catalog(db,unit_keys=('137',),document_format='pdf')
    assert any(e.hit.url=='https://ncsa.ru/actual-fixture.pdf' for e in data.entries)
    assert all(e.hit.url!='https://ncsa.ru/catalog-fixture/' for e in data.entries)


@pytest.mark.asyncio
@pytest.mark.parametrize('status',['no_match','unavailable'])
async def test_generic_pipeline_never_generates_without_source(status,settings):
    from src.core.rag.pipeline import answer_question
    from src.core.rag.retriever import RetrievalResult
    class Retriever:
        async def retrieve(self,*args,**kwargs):
            return RetrievalResult([],status)
    class Model:
        async def generate(self,*args,**kwargs):
            raise AssertionError('No evidence must not reach generation')
    answer=await answer_question('Вопрос об академии',Retriever(),Model(),settings)
    assert answer.hits==[]
    assert answer.usage['retrieval_status']==status
    assert ('техническая ошибка' in answer.answer)==(status=='unavailable')


def test_snapshot_importers_share_the_special_material_review_policy():
    from src.core.rag.knowledge_extract import needs_source_review
    assert needs_source_review('Положение о стипендиях','https://ncsa.ru/a.pdf',{'ocr':False})
    assert needs_source_review('Расписание','https://ncsa.ru/a.pdf',{'ocr':True})
    assert needs_source_review('Ведомость','https://ncsa.ru/a.xls',{'layout_review_required':True})
    assert not needs_source_review('Институт','https://ncsa.ru/institutes/137/',{'ocr':False})


@pytest.mark.asyncio
async def test_degraded_empty_multi_query_is_unavailable(monkeypatch,settings):
    from types import SimpleNamespace
    from src.core.rag.retriever import HybridRetriever
    async def state(*args,**kwargs):
        return SimpleNamespace(active_profile={'name':'legacy-v1','model':settings.embedding_model,'dimension':settings.embedding_dim})
    async def degraded(*args,**kwargs):
        return [],{'errors':{'embedding_0':'ConnectionError'}}
    monkeypatch.setattr('src.core.rag.retriever.state_for',state)
    monkeypatch.setattr('src.core.rag.multi_query.multi_query_search',degraded)
    result=await HybridRetriever(None,None,settings.model_copy(update={'retrieval_policy':'multi_query'})).retrieve('Вопрос')
    assert result.status=='unavailable'


def test_merged_html_table_preserves_raw_cells_without_inventing_column_facts():
    from tokenizers import Tokenizer,models,pre_tokenizers
    from src.core.rag.knowledge_extract import full_text
    tokenizer=Tokenizer(models.WordLevel({'[UNK]':0},unk_token='[UNK]'))
    tokenizer.pre_tokenizer=pre_tokenizers.Whitespace()
    body='<main><h1>Программы</h1><p>Профиль: Экология леса.</p><table><tr><th rowspan="2">Программа</th><th colspan="2">Места</th></tr><tr><th>Бюджет</th><th>Договор</th></tr><tr><td>Лесное дело</td><td>19</td><td>27</td></tr></table></main>'
    _,blocks,_,_=html_content(body,'https://ncsa.ru/programmes/',CrawlConfig())
    assert 'Лесное дело | 19 | 27' in full_text(blocks)
    chunks=semantic_chunks(blocks,'Программы',tokenizer)
    assert any('Экология леса' in c.content for c in chunks)
    assert all('19' not in c.content and '27' not in c.content for c in chunks)


def test_unlabelled_table_keeps_first_data_row_as_data():
    body='<main><h1>Таблица</h1><table><tr><td>Первое направление</td><td>42</td></tr><tr><td>Второе направление</td><td>17</td></tr></table></main>'
    _,blocks,_,_=html_content(body,'https://ncsa.ru/table/',CrawlConfig())
    table=[b.text for b in blocks if b.kind=='table']
    assert 'Колонка 1: Первое направление | Колонка 2: 42' in table[0]
    assert 'Колонка 1: Второе направление | Колонка 2: 17' in table[1]


@pytest.mark.asyncio
async def test_new_unverified_table_only_version_cannot_leave_old_flat_facts_active(db,settings):
    from src.core.db.models import Document,DocumentStatus,Chunk
    from tokenizers import Tokenizer,models
    config=CrawlConfig()
    url='https://ncsa.ru/layout-only-fixture/'
    old=Document(title='Таблица',source_type='ncsa_web',external_id=url,source_url=url,
        status=DocumentStatus.INDEXED,meta={'crawl_scope':config.scope})
    db.add(old);await db.flush()
    db.add(Chunk(document_id=old.id,chunk_index=0,content='Ошибочная прежняя привязка колонок',meta={}))
    await db.flush()
    crawler=DeepCrawler(config,'.',db=db,embedder=None,settings=settings,
        tokenizer=Tokenizer(models.WordLevel({'[UNK]':0},unk_token='[UNK]')))
    result=await crawler.ingest({'url':url,'suffix':''},'Таблица',
        [Block('Строка: программа | 17 | 42','Таблица',kind='unverified_table')],
        {'crawl_scope':config.scope},None)
    assert result['state']=='awaiting_layout_review'
    assert old.status==DocumentStatus.ARCHIVED
    assert old.meta['pending_source_review']['unverified_table_blocks']==1
    from src.core.rag.bm25_search import bm25_search
    found=await bm25_search(db,'Ошибочная прежняя привязка колонок',limit=20)
    assert all(hit.document_id!=old.id for hit in found)
    # No embedding call is possible above. The old record is preserved,
    # while retrieval excludes it through its indexed-status predicate.


@pytest.mark.asyncio
@pytest.mark.parametrize('payload,valid',[
    ('{"sources":[1],"complete":true}',True),
    ('{"sources":[2],"complete":true}',False),
    ('{"sources":[1],"complete":true,"answer":"Новая сумма 777"}',False),
    ('{broken',False),
])
async def test_source_quote_mode_stream_copies_only_selected_context_and_fails_closed(db,settings,payload,valid):
    from src.core.db.models import Document,DocumentStatus,Chunk,Message,MessageRole
    from src.core.rag.vector_search import SearchHit
    from src.core.rag.retriever import RetrievalResult
    from src.core.services.chat_service import ChatService
    from src.core.api.schemas import ChatRequest
    from src.core.llm.base import LLMResponse
    from sqlalchemy import select
    review={'scope':'spo_confirmed','text_verified':True,'currency':'unknown'}
    doc=Document(title='Проверенный отрывок',source_type='quote_fixture',external_id='quote-stream-fixture',
        source_url='https://ncsa.ru/source-quote-fixture/',status=DocumentStatus.INDEXED,
        meta={'scholarship_material':True,'review':review})
    db.add(doc);await db.flush()
    original='Условие: при наличии технической возможности. Слово в источнике: unknown.'
    chunk=Chunk(document_id=doc.id,chunk_index=0,content=original,meta={})
    db.add(chunk);await db.flush()
    hit=SearchHit(chunk.id,doc.id,original,doc.title,doc.source_url,1,1,'fixture',
        meta={'chunk_index':0,'scholarship_material':True,'document_review':review})
    class Retriever:
        async def retrieve(self,*args,**kwargs):return RetrievalResult([hit],'matched')
    class Model:
        model_name='fixture'
        async def generate_structured(self,messages,*args,**kwargs):
            assert original in str(messages)
            return LLMResponse(payload,{})
        async def generate(self,*args,**kwargs):raise AssertionError('Free generation is forbidden')
        async def stream(self,*args,**kwargs):
            raise AssertionError('Unchecked streaming is forbidden')
            yield
    question='Какое условие указано в источнике?'
    service=ChatService(db,Model(),Retriever(),settings.model_copy(update={'answer_mode':'source_quotes'}))
    events=[event async for event in service.stream(ChatRequest(message=question))]
    assert not any(e['type']=='error' for e in events)
    done=next(e['data'] for e in events if e['type']=='done')
    assert ''.join(e['text'] for e in events if e['type']=='delta')==done['answer']
    assert done['usage']['answer_mode']=='source_quotes'
    assert done['usage']['answer_text_origin']==('selected_source_text' if valid else 'fixed_fallback')
    if valid:
        assert original+' [1]' in done['answer']
        assert done['sources'][0]['chunk_id']==chunk.id
    else:
        assert done['usage']['fallback_type']=='answer_check_error'
        assert 'Не удалось завершить выбор' in done['answer']
    assert '777' not in done['answer']
    assert await db.scalar(select(Message.id).where(Message.role==MessageRole.USER,Message.content==question))
