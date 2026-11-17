from bs4 import BeautifulSoup
from src.core.rag.source_links import link_title


def test_generic_download_link_uses_actual_document_name():
    soup=BeautifulSoup('<table><tr><td>Перечень документов для поступления</td><td><a href="a.pdf">Открыть (PDF)</a></td></tr></table>','html.parser')
    assert link_title(soup.a)=='Перечень документов для поступления'


def test_descriptive_label_is_preserved_and_navigation_does_not_invent_title():
    soup=BeautifulSoup('<p><a href="a.pdf">Положение об общежитии</a></p>','html.parser')
    assert link_title(soup.a)=='Положение об общежитии'
    soup=BeautifulSoup('<p><a href="a.pdf">Скачать</a></p>','html.parser')
    assert link_title(soup.a)=='Скачать'
