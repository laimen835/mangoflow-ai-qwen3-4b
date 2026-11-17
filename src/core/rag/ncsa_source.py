"""Bounded public SPK source; shares the existing documents/chunks database."""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

import httpx
from sqlalchemy import select
from bs4 import BeautifulSoup

from src.core.rag.ingest import ingest_text
from src.core.db.models import Document, DocumentStatus
from src.core.services.directory import extract_teachers, extract_groups
from src.core.rag.source_links import link_title

SEED = "https://ncsa.ru/institutes/spk/inst.php?ELEMENT_ID=148"
START_PAGES = ["https://ncsa.ru/institutes/", *[f"https://ncsa.ru/institutes/{i}/" for i in (137,138,145,358,151,146,147,149,150)],
               "https://ncsa.ru/", SEED, "https://ncsa.ru/institutes/spk/postupayushchim-v-spk/",
               "https://ncsa.ru/institutes/spk/student/student.php",
               "https://ncsa.ru/institutes/spk/chairs/",
               "https://ncsa.ru/institutes/spk/dir.php"]
USER_AGENT = "NCSA-SPK-RAG/0.1 (local knowledge indexing)"


def canonical_url(href: str, base: str = SEED) -> str | None:
    if "@" in href or href.strip().lower().startswith(("tel", "mailto", "javascript")):
        return None
    parts = urlsplit(urljoin(base, href.strip()))
    if parts.scheme not in {"https", "http"} or parts.hostname not in {"ncsa.ru", "www.ncsa.ru"}:
        return None
    try:
        port = parts.port
    except ValueError:
        return None
    if port not in {None, 80, 443} or parts.username or parts.password:
        return None
    query = urlencode(sorted((k, v) for k, v in parse_qsl(parts.query)
                             if k == "ELEMENT_ID"))
    path = parts.path or "/"
    if path.endswith("/index.php"):
        path = path[:-len("index.php")]
    return urlunsplit(("https", "ncsa.ru", path, query, ""))


def allowed_url(url: str) -> bool:
    if canonical_url(url) != url:
        return False
    path = urlsplit(url).path.lower()
    if any(p in path for p in ('/bitrix/', '/auth/', '/login', '/logout', '/admin/', '/personal/', '/search/', '/ajax/', '/api/')):
        return False
    return not path.endswith(('.jpg','.jpeg','.png','.gif','.svg','.webp','.css','.js','.zip','.rar','.exe','.mp4','.mp3'))


def extract_page(html: str, url: str) -> tuple[str, str, list[tuple[str, str]]]:
    soup = BeautifulSoup(html, "html.parser")
    # NCSA's main body excludes the repeated academy-wide header/footer menus.
    root = soup.select_one("#ncsa-main .content_container") or soup.select_one("main")
    if root is None:
        root = soup.select_one("#ncsa-main")
    if root is None:
        raise ValueError("Main page content not found; refusing to index navigation")
    title_tag = root.find("h1") or soup.title
    title = title_tag.get_text(" ", strip=True) if title_tag else url
    from src.core.services.academy_scope import unit_for_url
    unit = unit_for_url(url)
    if not title.strip():
        title = unit.name if unit else url
    if title.strip().lower() in {"карта сайта", "страница не найдена", "404"}:
        raise ValueError("Soft 404 page")
    for tag in root.select("script, style, nav, footer, noscript, .breadcrumb, .breadcrumbs, button"):
        tag.decompose()
    links = []
    for address in root.select('.uni-director__contact-text'):
        if "черкесск" in address.get_text(" ", strip=True).lower():
            address.insert_before("Адрес: ")
    for anchor in root.select("a[href]"):
        if anchor["href"].startswith("tel:"):
            anchor.insert_before("Телефон: ")
        elif anchor["href"].startswith("mailto:"):
            anchor.insert_before("Электронная почта: ")
        target = canonical_url(anchor["href"], url)
        if target and allowed_url(target):
            label = link_title(anchor)
            links.append((target, label))
            # Preserve destinations so the assistant can navigate to actual documents.
            if label:
                anchor.append(f" ({target})")
    text = root.get_text("\n", strip=True)
    address = next((el for el in root.select('.uni-director__contact-text')
                    if "черкесск" in el.get_text(" ", strip=True).lower()), None)
    phone = root.select_one('a[href^="tel:"]')
    email = root.select_one('a[href="mailto:spk@ncsa.ru"]')
    if address and phone and email:
        text = ("Контакты среднепрофессионального колледжа СПК СКГА. "
                f"Адрес: {address.get_text(' ', strip=True)}. "
                f"Телефон: {phone.get_text(' ', strip=True)}. "
                f"Электронная почта: {email.get_text(' ', strip=True)}.\n\n" + text)
    return title, text, list(dict.fromkeys(links))


def extract_contacts(html: str) -> dict | None:
    soup = BeautifulSoup(html, "html.parser")
    root = soup.select_one("#ncsa-main .content_container")
    if root is None:
        return None
    address = next((el for el in root.select('.uni-director__contact-text')
                    if "черкесск" in el.get_text(" ", strip=True).lower()), None)
    phone = root.select_one('a[href^="tel:"]')
    email = root.select_one('a[href="mailto:spk@ncsa.ru"]')
    if not (address and phone and email):
        return None
    return {"address": address.get_text(" ", strip=True),
            "phone": phone.get_text(" ", strip=True),
            "email": email.get_text(" ", strip=True)}


def extract_director(html: str) -> str | None:
    root = BeautifulSoup(html, "html.parser")
    name = root.select_one(".uni-director__name")
    return name.get_text(" ", strip=True) if name else None


async def archive_missing(db, url, exc):
    if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code in {404, 410}:
        doc = (await db.execute(select(Document).where(Document.external_id == url,
            Document.source_type.in_(["ncsa_web", "ncsa_pdf"])))).scalar_one_or_none()
        if doc:
            doc.status = DocumentStatus.ARCHIVED
            await db.commit()


async def fetch_limited(client: httpx.AsyncClient, url: str) -> tuple[bytes, str]:
    # Follow only redirects that remain inside the source boundary.
    for _ in range(6):
        async with client.stream("GET", url) as response:
            if response.is_redirect:
                target = canonical_url(response.headers.get("location", ""), url)
                if not target or not allowed_url(target):
                    raise ValueError("Redirect leaves the configured source")
                url = target
                continue
            response.raise_for_status()
            chunks, size = [], 0
            async for chunk in response.aiter_bytes():
                size += len(chunk)
                if size > 20 * 1024 * 1024:
                    raise ValueError("Document exceeds 20 MB")
                chunks.append(chunk)
            return b"".join(chunks), response.headers.get("content-type", "")
    raise ValueError("Too many redirects")


async def sync_ncsa(db, embedder, settings, *, max_pages=48, max_pdfs=8) -> dict:
    from src.core.services.academy_scope import scope_metadata, unit_for_url
    frontier_file = Path(settings.raw_dir) / 'ncsa-frontier.json'
    try:
        previous = json.loads(frontier_file.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        previous = []
    # Refresh unit roots every pass, then resume public links left last time.
    queue = deque([(u, "", 0) for u in START_PAGES] + [tuple(x) for x in previous
        if isinstance(x,list) and len(x)==3 and isinstance(x[0],str) and allowed_url(x[0])
        and isinstance(x[2],int) and 0 <= x[2] <= 3])
    visited, pdf_links, results, errors = set(), {}, [], []
    retry_pages = []
    pdf_origins, checked_parent_links, archived = {}, {}, []
    profile_names = {}
    cache = Path(settings.raw_dir) / "ncsa-cache"
    cache.mkdir(parents=True, exist_ok=True)
    pdf_state_file = cache / 'crawl-pdfs.json'
    try:
        pdf_state = json.loads(pdf_state_file.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        pdf_state = {}
    for target, record in pdf_state.items():
        if allowed_url(target) and urlsplit(target).path.lower().endswith('.pdf'):
            pdf_links[target] = record.get('title', '')
            pdf_origins[target] = set(record.get('parents', []))
    async with httpx.AsyncClient(timeout=45, headers={"User-Agent": USER_AGENT}) as client:
        while queue and len(visited) < max_pages:
            url, label, depth = queue.popleft()
            if url in visited:
                continue
            visited.add(url)
            try:
                body, content_type = await fetch_limited(client, url)
                if "html" not in content_type:
                    raise ValueError("Expected an HTML page")
                html = body.decode("utf-8", errors="replace")
                title, text, links = extract_page(html, url)
                # Discover public sections from the site menu too, but never
                # index that repeated menu as evidence for page facts.
                for anchor in BeautifulSoup(html, 'html.parser').select('a[href]'):
                    target = canonical_url(anchor['href'], url)
                    if target and allowed_url(target) and target not in {u for u,_ in links}:
                        links.append((target, link_title(anchor)))
                if url in profile_names:
                    title = "Руководство · " + profile_names[url]
                if len(text.strip()) < 100:
                    raise ValueError("Page has no substantive text")
                if len(text) > 150000:
                    raise ValueError('Page exceeds 150,000 extracted characters; needs section-based import')
                result = await ingest_text(
                    db, title=title, text=text, source_type="ncsa_web", source_url=url,
                    external_id=url, meta={"source": "ncsa_academy", **scope_metadata(url, html), "fetched_at": datetime.now(timezone.utc).isoformat(),
                        "contacts": extract_contacts(html) if url == SEED else None,
                        "director": extract_director(html) if url == SEED else None,
                        "teachers": extract_teachers(html) if url == SEED else []},
                    embedder=embedder, settings=settings)
                results.append({"url": url, "title": title, **result})
                print(f"[page] {result['status']} {url} chunks={result['chunks_created']}", flush=True)
                for target, link_label in links:
                    path = urlsplit(target).path.lower()
                    if "/management/" in path and re.fullmatch(r"[А-ЯЁ][а-яё-]+ [А-ЯЁ][а-яё-]+ [А-ЯЁ][а-яё-]+", link_label):
                        profile_names[target] = link_label
                    if path.endswith(".pdf"):
                        pdf_links.setdefault(target, link_label)
                        pdf_origins.setdefault(target, set()).add(url)
                    elif depth < 3 and not path.endswith((".doc", ".xls", ".docx", ".xlsx", ".pptx", ".ppt")):
                        if not any(x in path for x in ("/edurpd", "/eduannotation", "/graduate/")):
                            if target not in visited and len(queue) < 10000:
                                queue.append((target, link_label, depth + 1))
                checked_parent_links[url] = {u for u, _ in links if urlsplit(u).path.lower().endswith(".pdf")}
            except Exception as exc:
                await db.rollback()
                await archive_missing(db, url, exc)
                errors.append({"url": url, "error": str(exc)})
                if not isinstance(exc, httpx.HTTPStatusError) or exc.response.status_code not in {404,410}:
                    retry_pages.append([url,label,depth])
                print(f"[error] {url}: {exc}", flush=True)
            await asyncio.sleep(0.5)

        # Prioritize the current timetable and calendar over historical orders.
        ordered = sorted(pdf_links.items(), key=lambda p: (
            0 if "расписание" in p[1].lower() else 1 if "календарный" in p[1].lower() else 2,
            pdf_state.get(p[0], {}).get('attempted_at', ''), p[0]))
        for url, title in ordered[:max_pdfs]:
            pdf_state[url] = {'title': title, 'parents': sorted(pdf_origins.get(url, [])),
                              'attempted_at': datetime.now(timezone.utc).isoformat()}
            try:
                body, _ = await fetch_limited(client, url)
                if not body.startswith(b"%PDF"):
                    raise ValueError("Response is not a PDF")
                import pymupdf
                with pymupdf.open(stream=body, filetype="pdf") as pdf:
                    if len(pdf) > 150:
                        raise ValueError("PDF exceeds 150 pages")
                    pages = [page.get_text(sort=True).strip() for page in pdf]
                text = "\n\n".join(f"Страница {i + 1}\n{page}" for i, page in enumerate(pages) if page)
                filename = hashlib.sha256(url.encode()).hexdigest()[:20] + ".pdf"
                (cache / filename).write_bytes(body)
                if sum(len(p) for p in pages) < max(100, len(pages) * 30):
                    from src.core.rag.ocr import recognize_pdf
                    pages = await asyncio.to_thread(recognize_pdf, body, settings.tesseract_cmd, cache)
                    if sum(len(p) for p in pages) < 100:
                        raise ValueError("OCR produced insufficient readable text")
                    text = "\n\n".join(f"Страница {i + 1}\n{page}" for i, page in enumerate(pages) if page)
                    extraction = "ocr"
                else:
                    extraction = "text"
                origins = pdf_origins.get(url, set())
                owners = {u.key: u for parent in origins if (u := unit_for_url(parent))}
                pdf_scope = scope_metadata(url)
                if len(owners) == 1 and not unit_for_url(url):
                    owner = next(iter(owners.values()))
                    pdf_scope.update(unit_key=owner.key, unit_name=owner.name)
                result = await ingest_text(
                    db, title=title or filename, text=text, source_type="ncsa_pdf", source_url=url,
                    external_id=url, meta={"source": "ncsa_academy", **pdf_scope, "pages": len(pages),
                        "extraction": extraction, "groups": extract_groups(text),
                        "cache_file": filename,
                        "linked_from": sorted(pdf_origins.get(url, [])),
                        "fetched_at": datetime.now(timezone.utc).isoformat()}, embedder=embedder, settings=settings)
                results.append({"url": url, "title": title, **result})
                print(f"[pdf] {result['status']} {url} chunks={result['chunks_created']}", flush=True)
            except Exception as exc:
                await db.rollback()
                await archive_missing(db, url, exc)
                errors.append({"url": url, "error": str(exc)})
                print(f"[error] {url}: {exc}", flush=True)
            await asyncio.sleep(0.5)
    # Only retire a removed PDF when its one known parent was fetched successfully.
    old_pdfs = (await db.execute(select(Document).where(Document.source_type == "ncsa_pdf",
        Document.status == DocumentStatus.INDEXED))).scalars().all()
    for doc in old_pdfs:
        parents = doc.meta.get("linked_from", [])
        if len(parents) == 1 and parents[0] in checked_parent_links and doc.source_url not in checked_parent_links[parents[0]]:
            doc.status = DocumentStatus.ARCHIVED
            archived.append(doc.source_url)
    await db.commit()
    frontier = list({u: [u,label,depth] for u,label,depth in queue if u not in visited}.values())
    frontier += [x for x in retry_pages if x[0] not in START_PAGES]
    frontier_file.parent.mkdir(parents=True, exist_ok=True)
    temporary = frontier_file.with_suffix('.tmp')
    temporary.write_text(json.dumps(frontier,ensure_ascii=False),encoding='utf-8')
    temporary.replace(frontier_file)
    for target, label in pdf_links.items():
        pdf_state.setdefault(target, {'title': label})['parents'] = sorted(pdf_origins.get(target, []))
    temporary = pdf_state_file.with_suffix('.tmp')
    temporary.write_text(json.dumps(pdf_state,ensure_ascii=False),encoding='utf-8')
    temporary.replace(pdf_state_file)
    return {"seed": SEED, "completed_at": datetime.now(timezone.utc).isoformat(),
            "limits": {"max_pages": max_pages, "max_pdfs": max_pdfs},
            "documents": results, "errors": errors,
            "pdfs_discovered": len(pdf_links), "remaining_page_links": len(frontier), "archived": archived,
            "coverage": "Bounded official-site crawl; remaining frontier resumes on the next update."}
