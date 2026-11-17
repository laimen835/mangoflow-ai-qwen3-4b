from collections import Counter
from src.core.rag.evidence_search import Catalog,Entry,word_terms,score_catalog,search_expansion
from src.core.rag.vector_search import SearchHit


def test_russian_inflections_match_despite_nbsp_and_capitalization():
    assert word_terms('Диплом дипломе дипломом')==Counter({'диплом':3})
    assert word_terms('Для иногородних\u00a0обучающихся')==word_terms('иногородние обучающиеся')


def test_bm25_uses_body_evidence_over_repeated_document_title():
    def entry(i,body):return Entry(SearchHit(i,1,'','Положение о помощи',None,1,0,'test'),Counter(body),Counter({'помощ':1}))
    rows=[entry(1,{'ректор':1,'решен':1,'помощ':1}),entry(2,{'помощ':20,'материальн':20})]
    data=Catalog(rows,Counter({'ректор':1,'решен':1,'помощ':2,'материальн':1}),21.5)
    ranked=score_catalog(data,Counter({'ректор':1,'решен':1,'помощ':1}))
    assert ranked[0][0].hit.chunk_id==1


def test_query_expansion_keeps_names_groups_codes_and_years():
    query='Как решает Иванов вопрос по группе 09.02.07-251 в 2027 году?'
    expanded=search_expansion(query)
    assert expanded.startswith(query) and 'решение' in expanded
    assert 'Иванов' in expanded and '09.02.07-251' in expanded and '2027' in expanded
