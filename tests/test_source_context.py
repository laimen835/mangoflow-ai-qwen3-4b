from dataclasses import replace
from src.core.rag.vector_search import SearchHit
from src.core.rag.source_context import prepare_source_hits
from src.core.rag.ncsa_source import SEED


def hit(ident, text, index, doc=1, url=SEED):
    return SearchHit(ident,doc,text,'Источник',url,1,1,'vector',meta={'chunk_index':index})


def test_reversed_retrieval_order_restores_contiguous_source_and_keeps_ids():
    overlap='при условии поступления на места '
    a=hit(1,'Бесплатное образование '+overlap,0)
    b=hit(2,overlap+'с финансированием из бюджета.',1)
    rows=prepare_source_hits([b,a], 'Обучение')
    assert len(rows)==1 and rows[0].chunk_id==2
    assert rows[0].content=='Бесплатное образование '+overlap+'с финансированием из бюджета.'
    assert rows[0].meta['supporting_chunk_ids']==[1,2]


def test_shared_phrases_cannot_join_different_documents_or_nonadjacent_fragments():
    a=hit(1,'Начало Общий фрагмент длиннее 16',0)
    for b in [hit(2,'Общий фрагмент длиннее 16 конец',3),hit(3,'Общий фрагмент длиннее 16 конец',1,doc=2)]:
        assert len(prepare_source_hits([a,b], 'Вопрос'))==2
    assert len(prepare_source_hits([a,hit(2,'Нет перекрытия',1)], 'Вопрос'))==2


def test_explicit_page_preference_never_fetches_or_substitutes_a_missing_source():
    pdf=hit(1,'Другой источник',0,url='https://ncsa.ru/a.pdf')
    page=hit(2,'Страница колледжа',0)
    assert prepare_source_hits([pdf,page],'Какие сведения на основной странице СПК?')==[page]
    assert len(prepare_source_hits([pdf,page],'Противоречат ли основная страница СПК и положение?'))==2
    assert prepare_source_hits([pdf,page],'Какие сведения на основной странице академии?')[0].url==pdf.url
    assert prepare_source_hits([pdf],'Какие сведения на основной странице СПК?')==[pdf]
