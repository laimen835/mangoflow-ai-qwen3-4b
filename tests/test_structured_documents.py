from io import BytesIO
import pytest
from src.core.rag.structured_documents import extract,Block,structural_chunks
from src.core.rag.scholarship_source import official_url,document_url,discover_grants,fetch_official
from src.core.rag.document_policy import exclusion_reasons
from types import SimpleNamespace


def test_docx_keeps_paragraph_order_and_table_headers():
    from docx import Document
    doc=Document();doc.add_heading('Поддержка',level=1);doc.add_paragraph('Первый абзац')
    table=doc.add_table(rows=2,cols=2)
    for cell,value in zip([*table.rows[0].cells,*table.rows[1].cells],['Выплата','Год','Помощь','2025']):cell.text=value
    doc.add_paragraph('Последний абзац');out=BytesIO();doc.save(out)
    blocks,method=extract(out.getvalue(),'.docx')
    assert method=='docx-smarttags-v2' and blocks[0].section=='Поддержка'
    assert blocks[0].text=='Первый абзац' and blocks[-1].text=='Последний абзац'
    assert 'Выплата: Помощь' in blocks[1].text and 'Год: 2025' in blocks[1].text


def test_docx_smart_tag_does_not_remove_years_or_leak_annotation_metadata():
    from docx import Document
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    doc=Document();table=doc.add_table(rows=2,cols=1)
    table.cell(0,0).text='Срок обучения'
    paragraph=table.cell(1,0).paragraphs[0]
    tag=OxmlElement('w:smartTag');tag.set(qn('w:element'),'metricconverter')
    props=OxmlElement('w:smartTagPr');attr=OxmlElement('w:attr')
    attr.set(qn('w:name'),'ProductID');attr.set(qn('w:val'),'not visible metadata')
    props.append(attr);tag.append(props)
    run=OxmlElement('w:r');text=OxmlElement('w:t');text.text='3 г';run.append(text);tag.append(run)
    paragraph._p.append(tag);paragraph.add_run('. 10 мес.')
    assert paragraph.text=='. 10 мес.'  # Reproduces the library-level loss.
    out=BytesIO();doc.save(out);blocks,method=extract(out.getvalue(),'.docx')
    assert method=='docx-smarttags-v2'
    assert blocks[0].text=='Срок обучения: 3 г. 10 мес.'
    assert 'not visible metadata' not in blocks[0].text


def test_xlsx_keeps_sheets_and_repeated_headers():
    from openpyxl import Workbook
    w=Workbook();s=w.active;s.title='СПО';s.append(['Вид','Размер']);s.append(['Помощь',123]);s.append(['Другая',456])
    out=BytesIO();w.save(out);blocks,_=extract(out.getvalue(),'.xlsx')
    assert len(blocks)==2 and all(b.sheet=='СПО' for b in blocks)
    assert 'Размер: 123' in blocks[0].text and 'Размер: 456' in blocks[1].text


def test_xlsx_does_not_guess_missing_formula_values():
    from openpyxl import Workbook
    w=Workbook();w.active.append(['Размер']);w.active.append(['=100+200'])
    out=BytesIO();w.save(out)
    with pytest.raises(ValueError,match='recalculation'):extract(out.getvalue(),'.xlsx')


def test_unapproved_or_changed_pilot_inputs_cannot_be_promoted():
    from scripts.scholarship_pilot import check_approval
    report={'promotion_approved':True,'variant':'structured','input_signature':'reviewed',
            'gates':{'correct_control_answers':4,'improved_control_answers':4,'unsupported_control_answers':0,
                     'p95_search_ms':100,'regressions_passed':True,'tests_passed':True,'guardrails_passed':True}}
    check_approval(report,'structured','reviewed')
    for changed in [{**report,'promotion_approved':False},{**report,'variant':'legacy'},{**report,'input_signature':'changed'}]:
        with pytest.raises(RuntimeError):check_approval(changed,'structured','reviewed')
    with pytest.raises(RuntimeError):check_approval({**report,'gates':{**report['gates'],'p95_search_ms':3001}},'structured','reviewed')


@pytest.mark.parametrize('suffix',['.doc','.xls'])
def test_legacy_office_is_not_parsed_as_html(suffix):
    with pytest.raises(ValueError,match='requires conversion'):extract(b'<html>Fake data</html>',suffix)


def test_conversion_failure_is_recorded_without_aborting_other_files(monkeypatch):
    from scripts.extract_scholarship_snapshot import extract_file
    from pathlib import Path
    path=Path('legacy.doc')
    monkeypatch.setattr(Path,'read_bytes',lambda self:b'legacy binary document')
    result=extract_file(path)
    assert result['method']=='conversion_required' and not result['blocks']
    assert 'requires conversion' in result['error']


def test_only_bounded_official_links_and_relevant_committees():
    with pytest.raises(ValueError):official_url('https://ncsa.ru@evil.example/a.pdf')
    assert not document_url('https://ncsa.ru/other/a.pdf')
    assert not document_url('https://ncsa.ru/students/social/%2e%2e/private.pdf')
    html='''<a href="/students/social/help.pdf">Положение о материальной помощи</a>
    <a href="/sveden/grants/a.pdf">Медицинский институт приказ о создании стипендиальной комиссии</a>
    <a href="/sveden/grants/b.pdf">СПК приказ о создании стипендиальной комиссии</a>'''
    urls=[u for u,_ in discover_grants(html)]
    assert len(urls)==2 and not any(u.endswith('/a.pdf') for u in urls)


@pytest.mark.asyncio
async def test_redirect_and_size_limits_are_enforced():
    import httpx
    async def redirect(request):return httpx.Response(302,headers={'location':'https://example.org/secret.pdf'})
    async with httpx.AsyncClient(transport=httpx.MockTransport(redirect)) as client:
        with pytest.raises(ValueError):await fetch_official(client,'https://ncsa.ru/students/social/a.pdf')
    async def large(request):return httpx.Response(200,content=b'a'*11)
    async with httpx.AsyncClient(transport=httpx.MockTransport(large)) as client:
        with pytest.raises(ValueError,match='20 MB'):await fetch_official(client,'https://ncsa.ru/students/social/a.pdf',max_bytes=10)


def test_review_does_not_treat_download_date_as_currency():
    doc=SimpleNamespace(title='Положение',source_type='ncsa_scholarship',meta={'scholarship_material':True,'fetched_at':'2026-11-08','review':{'scope':'unknown','text_verified':False}})
    assert len(exclusion_reasons(doc))==2


def test_structural_token_budget_and_page_provenance():
    # Tiny deterministic tokenizer tests boundary handling, not Nomic semantics.
    class Tokens:
        def __init__(self,text):self.ids=list(range(len(text)));self.offsets=[(i,i+1) for i in range(len(text))]
    class Tokenizer:
        def encode(self,text):return Tokens(text)
    blocks=[Block('Текст '*100,'Условия',page=4)]
    chunks=structural_chunks(blocks,'Материальная помощь',Tokenizer(),max_tokens=160)
    assert len(chunks)>1 and all(len(c.content)<=160 for c in chunks)
    assert all(c.meta['page']==4 and 'Условия' in c.content for c in chunks)
