from src.core.rag.chunker import RecursiveChunker
from src.core.rag.ncsa_source import SEED, allowed_url, canonical_url, extract_page, extract_contacts


def test_tracking_removed_and_document_identity_preserved():
    assert canonical_url(SEED + "&ysclid=tracking#contacts") == SEED
    assert canonical_url("/institutes/spk/index.php") == "https://ncsa.ru/institutes/spk/"
    assert canonical_url("https://other.example/page") is None
    assert canonical_url("baichorova@ncsa.ru") is None
    assert canonical_url("tel-") is None
    assert not allowed_url("https://ncsa.ru/bitrix/admin/index.php")


def test_extract_only_main_body_and_keep_document_link():
    html = '''<title>СПК</title><header>Чужое меню</header>
    <div id="ncsa-main"><div class="content_container">
    <h1>Колледж</h1><p>Адрес колледжа</p><script>ignore()</script>
    <a href="/upload/file.pdf">Расписание</a></div></div><footer>Чужой телефон</footer>'''
    title, text, links = extract_page(html, SEED)
    assert title == "Колледж" and "Адрес колледжа" in text
    assert "Чужое" not in text and "Чужой" not in text and "ignore" not in text
    assert ("https://ncsa.ru/upload/file.pdf", "Расписание") in links
    assert "https://ncsa.ru/upload/file.pdf" in text


def test_chunks_pack_short_facts_and_stay_bounded():
    text = "\n\n".join(f"Факт {i}: сведения о колледже." for i in range(80))
    chunks = RecursiveChunker(chunk_size=800, chunk_overlap=120).split(text)
    assert 2 < len(chunks) < 10
    assert all(len(c.content) <= 800 for c in chunks)
    assert all(f"Факт {i}:" in "\n".join(c.content for c in chunks) for i in range(80))


def test_contacts_do_not_mistake_phone_for_address():
    html = '''<div id="ncsa-main"><div class="content_container">
    <span class="uni-director__contact-text"><a href="tel:+78782293590">+7(8782) 29-35-90</a></span>
    <span class="uni-director__contact-text"><a href="mailto:spk@ncsa.ru">spk@ncsa.ru</a></span>
    <span class="uni-director__contact-text">г. Черкесск, пр. Ленина 152</span>
    </div></div>'''
    assert extract_contacts(html) == {"address": "г. Черкесск, пр. Ленина 152",
        "phone": "+7(8782) 29-35-90", "email": "spk@ncsa.ru"}
