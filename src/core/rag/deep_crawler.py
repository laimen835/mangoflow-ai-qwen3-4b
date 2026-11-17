"""Resumable, polite public crawler feeding the existing documents/chunks API.

SQLite is a private job manifest, not a replacement knowledge database. Every
request, including browser resources and sitemap redirects, uses the same policy.
"""
from __future__ import annotations

import asyncio
from collections import Counter
from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
import mimetypes
import os
from pathlib import Path
import sqlite3
import time
from urllib.parse import unquote, urlsplit
from urllib.robotparser import RobotFileParser
from xml.etree import ElementTree

import httpx

from src.core.rag.crawl_policy import CrawlConfig, document_suffix
from src.core.rag.knowledge_extract import VERSION, file_content, full_text, html_content, semantic_chunks, needs_source_review

USER_AGENT = 'MangoFlowKnowledgeBot/1.0 (public academic knowledge; bounded local indexing)'


def now():
    return datetime.now(timezone.utc).isoformat()


def atomic_json(path, value):
    path = Path(path)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(path)


async def validate_knowledge_binding(db, config):
    """Current backend uses one DB per organization/KB, never a fake row ACL."""
    from sqlalchemy import select
    from src.core.db.models import Document
    pairs=(await db.execute(select(Document.meta['organization_key'].astext,
        Document.meta['knowledge_base_key'].astext).distinct())).all()
    for organization,knowledge_base in pairs:
        if organization is None and knowledge_base is None:
            if config.scope!='ncsa/public-academy' or config.origin!='https://ncsa.ru':
                raise ValueError('Legacy data require the existing NCSA knowledge boundary')
        elif (organization,knowledge_base)!=(config.organization,config.knowledge_base):
            raise ValueError('Use a separate database for another organization or knowledge base')


@contextmanager
def job_lock(path):
    handle = Path(path).open('a+b')
    try:
        handle.seek(0, 2)
        if handle.tell() == 0:
            handle.write(b'0')
            handle.flush()
        handle.seek(0)
        try:
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except ImportError:
            import fcntl
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        handle.close()


class Manifest:
    def __init__(self, path, config, database_binding='download-only'):
        self.blocked = False
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('CREATE TABLE IF NOT EXISTS config (id INTEGER PRIMARY KEY, identity TEXT NOT NULL, binding TEXT NOT NULL)')
        saved = self.db.execute('SELECT identity,binding FROM config WHERE id=1').fetchone()
        if saved and (saved['identity'] != config.identity or saved['binding'] != database_binding):
            self.db.close()
            raise ValueError('Manifest belongs to another crawl boundary or database')
        self.db.execute('INSERT OR IGNORE INTO config VALUES (1,?,?)', (config.identity, database_binding))
        self.db.execute('''CREATE TABLE IF NOT EXISTS resources (
            url TEXT PRIMARY KEY, label TEXT NOT NULL, depth INTEGER NOT NULL,
            suffix TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0,
            etag TEXT, modified TEXT, raw_hash TEXT, text_hash TEXT, document_id INTEGER,
            data TEXT NOT NULL DEFAULT '{}', error TEXT, http_status INTEGER,
            checked_at TEXT, next_attempt REAL NOT NULL DEFAULT 0)''')
        self.db.execute('CREATE TABLE IF NOT EXISTS edges (parent TEXT, child TEXT, label TEXT, PRIMARY KEY(parent,child))')
        self.db.execute('CREATE TABLE IF NOT EXISTS events (at TEXT, url TEXT, kind TEXT, details TEXT)')
        self.db.execute("UPDATE resources SET state='pending' WHERE state='fetching'")
        self.db.commit()
        self.config = config

    def add(self, url, label='', depth=0, parent=''):
        count = self.db.execute('SELECT count(*) FROM resources').fetchone()[0]
        if count >= self.config.max_urls and not self.db.execute('SELECT 1 FROM resources WHERE url=?', (url,)).fetchone():
            self.event(parent, 'discovery_limit', {})
            return
        self.db.execute('INSERT OR IGNORE INTO resources(url,label,depth,suffix) VALUES (?,?,?,?)', (url, label, depth, document_suffix(url)))
        if parent:
            self.db.execute('INSERT OR IGNORE INTO edges VALUES (?,?,?)', (parent, url, label))
        self.db.commit()

    def event(self, url, kind, details):
        self.db.execute('INSERT INTO events VALUES (?,?,?,?)', (now(), url, kind, json.dumps(details, ensure_ascii=False)))
        self.db.commit()

    def update(self, url, **values):
        if 'data' in values:
            values['data'] = json.dumps(values['data'], ensure_ascii=False)
        keys = list(values)
        self.db.execute('UPDATE resources SET ' + ','.join(k+'=?' for k in keys) + ' WHERE url=?', [values[k] for k in keys]+[url])
        self.db.commit()

    def next(self, pages, documents):
        return self.db.execute('''SELECT * FROM resources WHERE state IN ('pending','retry')
            AND attempts < ? AND next_attempt <= ? AND ((suffix='' AND ? < ?) OR (suffix<>'' AND ? < ?))
            ORDER BY depth, CASE WHEN url LIKE ? THEN 0 WHEN suffix<>'' THEN 1
              WHEN url LIKE '%/structure/chairs/%' THEN 2 ELSE 3 END, rowid LIMIT 1''',
            (self.config.attempts, time.time(), pages, self.config.max_pages, documents, self.config.max_documents,
             self.config.seeds[0].split('?')[0]+'%')).fetchone()

    def refresh(self):
        # Last known content and HTTP validators survive the new generation.
        self.db.execute("UPDATE resources SET state='pending',attempts=0,next_attempt=0,error=NULL WHERE state<>'fetching'")
        self.db.commit()

    def report(self):
        rows = [dict(r) for r in self.db.execute('SELECT * FROM resources ORDER BY rowid')]
        states = Counter(r['state'] for r in rows)
        data = [json.loads(r['data']) for r in rows]
        return {'completed_at': now(), 'scope': self.config.scope, 'identity': self.config.identity, 'blocked':self.blocked,
                'limits': asdict(self.config), 'discovered': len(rows),
                'pages_discovered': sum(not r['suffix'] for r in rows), 'documents_discovered': sum(bool(r['suffix']) for r in rows),
                'downloaded_resources': sum(bool(r['raw_hash']) for r in rows),
                'downloaded_documents': sum(bool(r['raw_hash']) and bool(r['suffix']) for r in rows),
                'indexed_resources': states['indexed'], 'indexed_chunks': sum(d.get('chunks', 0) for r,d in zip(rows,data) if r['state']=='indexed'),
                'eligible_indexed_resources':sum(r['state']=='indexed' and d.get('eligible') is True for r,d in zip(rows,data)),
                'quarantined_indexed_resources':sum(r['state']=='indexed' and d.get('eligible') is False for r,d in zip(rows,data)),
                'duplicates': states['duplicate'], 'unavailable': states['gone'], 'states': dict(states),
                'remaining': sum(v for k,v in states.items() if k in {'pending','retry','fetching'}),
                'errors': [{'url':r['url'], 'kind':r['state'], 'error':r['error']} for r in rows if r['error']],
                'events': [dict(r) for r in self.db.execute('SELECT * FROM events')],
                'documents': [{'url':r['url'],'state':r['state'],'http_status':r['http_status'], 'checked_at':r['checked_at'],
                               'raw_sha256':r['raw_hash'],'text_sha256':r['text_hash'],'document_id':r['document_id'], **d} for r,d in zip(rows,data)],
                'coverage': 'Budgeted public snapshot. Pending, denied and failed resources are not claimed as read. Download time does not establish document validity.'}


class FetchFailure(Exception):
    def __init__(self, kind, status=None, retry_after=0):
        super().__init__(kind)
        self.kind, self.status, self.retry_after = kind, status, retry_after


class PoliteFetcher:
    def __init__(self, client, config, manifest):
        self.client, self.config, self.manifest = client, config, manifest
        self.robots = None
        self.delay = config.delay
        self.last_request = 0
        self._request_lock = asyncio.Lock()

    async def fetch(self, url, *, headers=None, robots=True, asset=False, limit=None):
        limit = limit or self.config.max_bytes
        for _ in range(6):
            if self.config.canonical(url, asset=asset) != url:
                raise FetchFailure('unsafe_url')
            if robots and (self.robots is None or not self.robots.can_fetch(USER_AGENT, url)):
                raise FetchFailure('robots_denied')
            async with self._request_lock:
                await asyncio.sleep(max(0, self.delay - (time.monotonic()-self.last_request)))
                self.last_request = time.monotonic()
            async with self.client.stream('GET', url, headers=headers or {}) as response:
                if response.status_code in {301,302,303,307,308}:
                    target = self.config.canonical(response.headers.get('location',''), url, asset=asset)
                    if not target:
                        raise FetchFailure('unsafe_redirect', response.status_code)
                    url = target
                    headers = {}  # Never forward validators across resource identity.
                    continue
                status = response.status_code
                metadata = {k: response.headers.get(k) for k in ('content-type','etag','last-modified')}
                metadata['final_url'] = url
                if status == 304:
                    return status, b'', metadata
                if status >= 400:
                    raw_retry = response.headers.get('retry-after','')
                    try:
                        retry = float(raw_retry)
                    except ValueError:
                        from email.utils import parsedate_to_datetime
                        try:
                            retry = parsedate_to_datetime(raw_retry).timestamp()-time.time()
                        except (TypeError, ValueError, OverflowError):
                            retry = 30
                    raise FetchFailure('http_'+str(status), status, max(1, retry))
                if int(response.headers.get('content-length','0')) > limit:
                    raise FetchFailure('size_limit', status)
                chunks, size = [], 0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > limit:
                        raise FetchFailure('size_limit', status)
                    chunks.append(chunk)
                return status, b''.join(chunks), metadata
        raise FetchFailure('redirect_limit')

    async def initialize(self):
        url = self.config.origin + '/robots.txt'
        sitemaps = []
        parser = RobotFileParser()
        try:
            status, body, headers = await self.fetch(url, robots=False, limit=1024*1024)
            text = body.decode('utf-8-sig', errors='replace')
            if 'html' in (headers.get('content-type') or '') or text.lstrip().startswith('<'):
                raise FetchFailure('invalid_robots')
            parser.parse(text.splitlines())
            sitemaps = [line.split(':',1)[1].strip() for line in text.splitlines() if line.lower().startswith('sitemap:')]
            self.delay = max(self.delay, parser.crawl_delay(USER_AGENT) or 0, parser.crawl_delay('*') or 0)
            rate = parser.request_rate(USER_AGENT) or parser.request_rate('*')
            if rate:
                self.delay = max(self.delay, rate.seconds / rate.requests)
            self.manifest.event(url, 'robots_loaded', {'status':status, 'delay':self.delay})
        except FetchFailure as exc:
            self.manifest.event(url, exc.kind, {'http_status':exc.status})
            if exc.status in {404,410}:
                parser.parse(['User-agent: *','Allow: /'])
            else:
                # Denied, malformed, timeout and server failure never mean permission.
                raise
        self.robots = parser
        return sitemaps or [self.config.origin+'/sitemap.xml', self.config.origin+'/sitemap_index.xml']


async def discover_sitemaps(fetcher, manifest, urls):
    queue, seen = list(urls), set()
    while queue and len(seen) < fetcher.config.max_sitemaps:
        raw = queue.pop(0)
        url = fetcher.config.canonical(raw)
        if not url or url in seen:
            continue
        seen.add(url)
        try:
            _, body, _ = await fetcher.fetch(url, limit=4*1024*1024)
            if b'<!DOCTYPE' in body.upper() or b'<!ENTITY' in body.upper():
                raise ValueError('Sitemap entities are prohibited')
            tree = ElementTree.fromstring(body)
            tag = tree.tag.rsplit('}',1)[-1]
            if tag not in {'sitemapindex','urlset'}:
                raise ValueError('Not a sitemap')
            for loc in tree.iter():
                if loc.tag.rsplit('}',1)[-1] != 'loc':
                    continue
                target = fetcher.config.canonical(loc.text or '')
                if not target:
                    continue
                if tag == 'sitemapindex':
                    if len(queue) < fetcher.config.max_sitemaps:
                        queue.append(target)
                else:
                    manifest.add(target, depth=1, parent=url)
            manifest.event(url, 'sitemap_loaded', {'kind':tag})
        except Exception as exc:
            manifest.event(url, 'sitemap_unavailable', {'reason':getattr(exc,'kind',type(exc).__name__), 'http_status':getattr(exc,'status',None)})


async def render_html(url, fetcher):
    """Optional Playwright renderer. Browser traffic stays inside GET/robots/budgets."""
    from playwright.async_api import async_playwright
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True,channel='msedge' if os.name=='nt' else None)
        try:
            context = await browser.new_context(service_workers='block', accept_downloads=False, user_agent=USER_AGENT)
            requests = 0

            async def route_request(route):
                nonlocal requests
                request = route.request
                requests += 1
                target = fetcher.config.canonical(request.url, asset=True)
                if request.method != 'GET' or requests > 80 or not target or request.resource_type in {'image','media','font','websocket'}:
                    await route.abort()
                    return
                try:
                    _, body, headers = await fetcher.fetch(target, asset=True, limit=5*1024*1024)
                    await route.fulfill(status=200, body=body, content_type=headers.get('content-type') or 'application/octet-stream')
                except Exception:
                    await route.abort()

            await context.route('**/*', route_request)
            page = await context.new_page()
            await page.goto(url, wait_until='domcontentloaded', timeout=120000)
            await page.wait_for_timeout(1500)
            html = await page.content()
            if len(html.encode()) > fetcher.config.max_bytes:
                raise ValueError('Rendered page exceeds size limit')
            return html
        finally:
            await browser.close()


class DeepCrawler:
    def __init__(self, config, folder, *, db=None, embedder=None, settings=None, tokenizer=None, office_executable='', client=None):
        self.config, self.folder = config, Path(folder)
        self.db, self.embedder, self.settings = db, embedder, settings
        self._base_embedder=embedder
        self.tokenizer, self.office_executable, self.client = tokenizer, office_executable, client
        self.folder.mkdir(parents=True, exist_ok=True)

    async def archive(self, record):
        if self.db is None or not record['document_id']:
            return
        from src.core.db.models import Document, DocumentStatus
        doc = await self.db.get(Document, record['document_id'])
        if doc and (doc.meta or {}).get('crawl_scope') == self.config.scope:
            doc.status = DocumentStatus.ARCHIVED
            await self.db.commit()

    async def ingest(self, record, title, blocks, metadata, original_path):
        from sqlalchemy import select
        from src.core.db.models import Document
        from src.core.rag.ingest import ingest_text
        from src.core.rag.document_policy import exclusion_reasons
        metadata = {**metadata, 'unverified_table_blocks':sum(b.kind=='unverified_table' for b in blocks),
                    'unverified_tables_in_index':False}
        source_type = 'ncsa_pdf' if record['suffix']=='.pdf' else 'ncsa_document' if record['suffix'] else 'ncsa_web'
        old = await self.db.scalar(select(Document).where(Document.source_type==source_type, Document.external_id==record['url']))
        # Never inherit approval from an old file or clear its quarantine through
        # another importer. Existing reviewed records stay intact for manual review.
        if old and ((old.meta or {}).get('review') or
                    (((old.meta or {}).get('review_required') or (old.meta or {}).get('scholarship_material'))
                     and (old.meta or {}).get('crawl_scope') != self.config.scope)):
            return {'state':'review_conflict', 'document_id':old.id, 'chunks':0}
        if old and ((old.meta or {}).get('review_required') or (old.meta or {}).get('scholarship_material')):
            metadata['review_required']=True
            if (old.meta or {}).get('scholarship_material'):
                metadata['scholarship_material']=True
        if old and (old.meta or {}).get('source_review'):
            from src.core.rag.document_policy import source_review_valid
            proof={**metadata,'source_review':old.meta['source_review']}
            if source_review_valid(proof,hashlib.sha256(full_text(blocks).encode()).hexdigest()):
                metadata['source_review']=old.meta['source_review']
        from src.core.rag.ncsa_source import SEED
        if record['url'] == SEED:
            # The existing college field readers reconstruct contiguous text.
            # Preserve their format while storing the full structural sidecar.
            from src.core.rag.chunker import RecursiveChunker
            chunks=RecursiveChunker(self.settings.chunk_size,self.settings.chunk_overlap).split(full_text(blocks),base_meta={'title':title})
            processing_version=None
        else:
            chunks = semantic_chunks(blocks, title, self.tokenizer)
            processing_version=VERSION
            if not chunks:
                # A changed page consisting only of an unverified table must
                # not leave its older, unsafe flat table in the active index.
                # Preserve the old record/chunks and the new raw sidecars for
                # review; this is not an embedding/transport failure.
                if old and (old.meta or {}).get('crawl_scope')==self.config.scope:
                    from src.core.db.models import DocumentStatus
                    old.status=DocumentStatus.ARCHIVED
                    old.meta={**old.meta,'pending_source_review':metadata,
                              'crawl_quarantine_reason':'no_verified_indexable_blocks'}
                    await self.db.commit()
                return {'state':'awaiting_layout_review','document_id':old.id if old else None,
                        'chunks':0,'eligible':False,'quarantine_reason':'no_verified_indexable_blocks',
                        'unverified_table_blocks':metadata['unverified_table_blocks']}
        result = await ingest_text(self.db, title=title, text=full_text(blocks), source_type=source_type,
                                   source_url=record['url'], external_id=record['url'], meta=metadata,
                                   embedder=self.embedder, settings=self.settings, prepared_chunks=chunks,
                                   processing_version=processing_version)
        doc = await self.db.get(Document, result['document_id'])
        if record['suffix']:
            storage = Path(self.settings.storage_dir).resolve()
            target = storage / 'originals' / original_path.name
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.exists():
                target.write_bytes(original_path.read_bytes())
            doc.file_path = target.relative_to(storage).as_posix()
            doc.file_name = Path(unquote(urlsplit(record['url']).path)).name[:200]
            doc.file_size = target.stat().st_size
            doc.file_mime = mimetypes.guess_type(doc.file_name)[0] or 'application/octet-stream'
            await self.db.commit()
        return {'state':'indexed', 'document_id':doc.id, 'chunks':len(chunks), 'ingest_status':result['status'], 'eligible':not exclusion_reasons(doc)}

    async def process(self, record, fetcher, manifest):
        url = record['url']
        old_data = json.loads(record['data'])
        conditional = {k:v for k,v in [('If-None-Match',record['etag']),('If-Modified-Since',record['modified'])] if v}
        status, body, headers = await fetcher.fetch(url, headers=conditional)
        if status == 304:
            prior = old_data.get('original_file')
            if not prior or not Path(prior).is_file():
                status, body, headers = await fetcher.fetch(url)
            else:
                body = Path(prior).read_bytes()
                headers['content-type'] = old_data.get('content_type','')
        suffix = record['suffix']
        # Extensionless file links are still documents, with the URL retained.
        if not suffix and body.startswith(b'%PDF'):
            suffix = '.pdf'
        record = {**dict(record), 'suffix':suffix}
        raw_hash = hashlib.sha256(body).hexdigest()
        original = self.folder / 'originals' / (raw_hash + (suffix or '.html'))
        original.parent.mkdir(exist_ok=True)
        if not original.exists():
            original.write_bytes(body)
        fetched_at = now()
        base = {'original_file':str(original), 'content_type':headers.get('content-type'), 'final_url':headers.get('final_url',url)}
        manifest.update(url, raw_hash=raw_hash, data={**old_data, **base}, http_status=status, checked_at=fetched_at,
                        etag=headers.get('etag') or record['etag'], modified=headers.get('last-modified') or record['modified'], suffix=suffix)
        from src.core.services.academy_scope import scope_metadata, linked_department_scope
        metadata = scope_metadata(url)
        if not suffix:
            title, blocks, links, dates = html_content(body, url, self.config)
            if self.config.render:
                try:
                    rendered = await render_html(url, fetcher)
                    title, blocks, rendered_links, dates = html_content(rendered, url, self.config)
                    links = list(dict.fromkeys(links + rendered_links))
                    render_path = self.folder / 'originals' / (hashlib.sha256(rendered.encode()).hexdigest()+'.rendered.html')
                    render_path.write_text(rendered, encoding='utf-8')
                    base.update(rendered_file=str(render_path))
                    manifest.event(url, 'rendered', {})
                except Exception as exc:
                    base['dynamic_error'] = type(exc).__name__
                    manifest.event(url, 'render_failed', {'reason':type(exc).__name__})
                    # Static text can be retained but is explicitly incomplete.
            for target,label in links:
                if record['depth'] < self.config.max_depth:
                    manifest.add(target, label, record['depth']+1, url)
                else:
                    manifest.event(url, 'depth_limit', {})
            metadata.update(scope_metadata(url, body.decode('utf-8', errors='replace')))
            from src.core.rag.ncsa_source import SEED, extract_contacts, extract_director
            if url == SEED:
                from src.core.services.directory import extract_teachers
                from src.core.rag.ncsa_source import extract_page
                from src.core.rag.structured_documents import Block
                html = body.decode('utf-8', errors='replace')
                metadata.update(contacts=extract_contacts(html),director=extract_director(html),teachers=extract_teachers(html))
                title,legacy_text,_=extract_page(html,url)
                blocks=[Block(legacy_text,title)]
            references = [{'parent':r['parent'], 'url':url, 'title':r['label']} for r in manifest.db.execute('SELECT parent,label FROM edges WHERE child=?',(url,))]
            scope = linked_department_scope(url, references)
            if scope.get('scope_evidence'):
                metadata.update(scope)
                metadata['department_name'] = title
            extraction = {'extraction':'html-rendered' if base.get('rendered_file') else 'html', 'ocr':False}
        else:
            title = record['label'] or Path(unquote(urlsplit(url).path)).name
            dates = {'published_at':None, 'source_updated_at':None}
            blocks, extraction = await asyncio.to_thread(file_content, body, suffix,
                tesseract=self.settings.tesseract_cmd if self.settings else '', cache=self.folder,
                ocr_pages=self.config.ocr_pages, office_executable=self.office_executable)
        text = full_text(blocks)
        if len(text.strip()) < 40:
            raise ValueError('Insufficient extracted text')
        if len(text) > 5_000_000:
            raise ValueError('Extracted text exceeds limit')
        text_hash = hashlib.sha256(text.encode()).hexdigest()
        texts = self.folder / 'text'
        texts.mkdir(exist_ok=True)
        text_path = texts / (text_hash+'.txt')
        text_path.write_text(text, encoding='utf-8')
        blocks_path = texts / (text_hash+'.blocks.json')
        atomic_json(blocks_path, [asdict(b) for b in blocks])
        special = needs_source_review(title,url,extraction)
        metadata.update(**dates, source='ncsa_academy', crawl_scope=self.config.scope,
            organization_key=self.config.organization, knowledge_base_key=self.config.knowledge_base,
            category=urlsplit(url).path.strip('/').split('/')[0], document_type=suffix[1:] or 'html',
            fetched_at=fetched_at, http_last_modified=headers.get('last-modified'), raw_sha256=raw_hash,
            extraction_version=VERSION, full_text_file=str(text_path), blocks_file=str(blocks_path),
            review_policy='source_text_v1',review_required=special, **extraction)
        # Dedup storage and same-scope index material; a duplicate URL remains
        # in the manifest with its exact provenance instead of another vector set.
        duplicate = None
        for row in manifest.db.execute("SELECT url,document_id,data FROM resources WHERE text_hash=? AND url<>? AND state IN ('indexed','extracted')", (text_hash,url)):
            data = json.loads(row['data'])
            if data.get('unit_key') == metadata.get('unit_key') and data.get('text_sha256') == text_hash:
                duplicate = row
                break
        result = {'state':'extracted', 'document_id':None, 'chunks':0}
        if duplicate and not record['document_id']:
            result.update(state='duplicate', document_id=duplicate['document_id'], duplicate_of=duplicate['url'])
        elif self.db is not None:
            result = await self.ingest(record, title, blocks, metadata, original)
        data = {**base, **result, 'title':title,'unit_key':metadata.get('unit_key'), 'text_file':str(text_path),
                'text_sha256':text_hash,'blocks_file':str(blocks_path), 'text_characters':len(text), **extraction}
        manifest.update(url, state=result['state'], document_id=result['document_id'], text_hash=text_hash, data=data, error=None)

    async def run(self, *, refresh=False, validate_dns=True):
        if self.db is not None and (self.tokenizer is None or self.settings is None or self.embedder is None):
            raise ValueError('Indexing requires settings, embedder and a local tokenizer')
        if self.db is not None:
            await validate_knowledge_binding(self.db,self.config)
            await self.db.rollback()
        if validate_dns:
            await asyncio.to_thread(self.config.check_public_dns)
        if self.settings:
            from sqlalchemy.engine import make_url
            binding = hashlib.sha256(make_url(self.settings.database_url).render_as_string(hide_password=False).encode()).hexdigest()
        else:
            binding = 'download-only'
        with job_lock(self.folder/'crawl.lock'):
            manifest = Manifest(self.folder/'manifest.sqlite3', self.config, binding)
            checkpoint=None
            try:
                if self.db is not None and self.settings.embedding_provider=='openai' and __import__('urllib.parse',fromlist=['urlsplit']).urlsplit(self.settings.openai_base_url).hostname in {'127.0.0.1','localhost'}:
                    from src.core.rag.embedding_cache import CheckpointEmbeddings,checkpoint_identity
                    identity=await checkpoint_identity(self.db,self.settings)
                    await self.db.rollback()  # No index lock is held during crawling.
                    checkpoint=CheckpointEmbeddings(self._base_embedder,self.folder/'embeddings.sqlite3',identity)
                    self.embedder=checkpoint
                if refresh:
                    manifest.refresh()
                for seed in self.config.seeds:
                    manifest.add(self.config.canonical(seed))
                own_client = self.client is None
                client = self.client or httpx.AsyncClient(timeout=httpx.Timeout(45, connect=15), follow_redirects=False, headers={'User-Agent':USER_AGENT})
                try:
                    fetcher = PoliteFetcher(client, self.config, manifest)
                    try:
                        maps = await fetcher.initialize()
                    except Exception as exc:
                        manifest.event(self.config.origin, 'crawl_blocked_by_robots', {'reason':getattr(exc,'kind',type(exc).__name__)})
                        manifest.blocked = True
                        report = manifest.report()
                        report['blocked'] = True
                        atomic_json(self.folder/'report.json', report)
                        return report
                    await discover_sitemaps(fetcher, manifest, maps)
                    pages = documents = 0
                    while record := manifest.next(pages, documents):
                        url = record['url']
                        pages += not bool(record['suffix'])
                        documents += bool(record['suffix'])
                        manifest.update(url, state='fetching', attempts=record['attempts']+1)
                        backoff=False
                        try:
                            await self.process(record, fetcher, manifest)
                        except asyncio.CancelledError:
                            manifest.update(url, state='pending')
                            raise
                        except Exception as exc:
                            if self.db is not None:
                                await self.db.rollback()
                            status = getattr(exc,'status',None)
                            state = 'gone' if status in {404,410} else 'denied' if getattr(exc,'kind','')=='robots_denied' else 'retry'
                            if state == 'gone':
                                await self.archive(record)
                            if state == 'retry' and record['attempts']+1 >= self.config.attempts:
                                state = 'failed'
                            kind = getattr(exc,'kind',type(exc).__name__)
                            # Exception text may contain private paths/HTTP headers;
                            # operational reports use a controlled class/reason only.
                            manifest.update(url, state=state, error=kind, http_status=status,
                                            next_attempt=time.time()+max(30,getattr(exc,'retry_after',0)), checked_at=now())
                            manifest.event(url, state, {'reason':kind,'http_status':status})
                            backoff=status in {429,503}
                        report = manifest.report()
                        atomic_json(self.folder/'report.json', report)
                        print(json.dumps({'url':url,'states':report['states'],'remaining':report['remaining']}, ensure_ascii=False), flush=True)
                        if backoff:
                            manifest.event(url,'origin_backoff',{'http_status':status})
                            break
                    return manifest.report()
                finally:
                    if own_client:
                        await client.aclose()
            finally:
                if checkpoint is not None:
                    checkpoint.close()
                    self.embedder=self._base_embedder
                atomic_json(self.folder/'report.json', manifest.report())
                manifest.db.close()
