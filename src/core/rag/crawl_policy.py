"""Explicit public-resource boundary shared by HTTP, sitemap and browser fetches."""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import ipaddress
import json
import posixpath
import re
import socket
from urllib.parse import parse_qsl, quote, unquote, urlencode, urljoin, urlsplit, urlunsplit

DOCUMENT_SUFFIXES = {'.pdf', '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx'}
BLOCKED_SUFFIXES = {'.jpg', '.jpeg', '.png', '.gif', '.svg', '.webp', '.ico', '.css', '.js', '.zip', '.rar', '.exe', '.mp4', '.mp3', '.7z'}
PRIVATE_PATH = re.compile(r'/(?:bitrix|auth|login|logout|admin|personal|search|api|ajax)(?:/|\.|$)', re.I)
QUERY_KEY = re.compile(r'(?:ELEMENT_ID|SECTION_ID|ID|PAGEN_\d+|page|p|year|SECTION_CODE)', re.I)


@dataclass(frozen=True)
class CrawlConfig:
    seeds: tuple[str, ...] = ('https://ncsa.ru/institutes/137/',)
    origin: str = 'https://ncsa.ru'
    organization: str = 'ncsa'
    knowledge_base: str = 'public-academy'
    max_pages: int = 200
    max_documents: int = 100
    max_depth: int = 20
    max_urls: int = 25000
    max_bytes: int = 20 * 1024 * 1024
    max_sitemaps: int = 30
    delay: float = 1.0
    attempts: int = 3
    render: bool = False
    ocr_pages: int = 30

    def __post_init__(self):
        origin = urlsplit(self.origin)
        if (origin.scheme != 'https' or not origin.hostname or origin.username or origin.password
                or origin.port not in (None, 443) or origin.path or origin.query or origin.fragment):
            raise ValueError('An explicit HTTPS origin is required')
        if not re.fullmatch(r'[a-z0-9_-]{1,64}', self.organization + '') or not re.fullmatch(r'[a-z0-9_-]{1,64}', self.knowledge_base):
            raise ValueError('Invalid organization/knowledge-base identifier')
        if not (0 <= self.max_depth <= 100 and 1 <= self.max_urls <= 100000
                and 0 < self.max_pages <= 10000 and 0 <= self.max_documents <= 10000
                and self.delay >= 1 and 1 <= self.attempts <= 5 and 1 <= self.ocr_pages <= 150):
            raise ValueError('Unsafe crawl limits')
        if not self.seeds or any(not self.canonical(seed) for seed in self.seeds):
            raise ValueError('Seed leaves the configured public origin')

    @property
    def scope(self):
        return f'{self.organization}/{self.knowledge_base}'

    @property
    def identity(self):
        # Budgets can change while resuming; the resource boundary cannot.
        value = {'origin': self.origin, 'seeds': self.seeds, 'scope': self.scope}
        return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()

    def canonical(self, value, base=None, *, asset=False):
        try:
            if not isinstance(value, str) or re.search(r'[\x00-\x1f\\]', value.strip()):
                return None
            if re.fullmatch(r'[\w.+-]+@[\w.-]+\.[a-zA-Z]{2,}',value.strip()):
                return None
            parts = urlsplit(urljoin(base or self.origin + '/', value.strip()))
            target_host = urlsplit(self.origin).hostname
            if (parts.scheme not in {'http', 'https'} or parts.hostname not in {target_host, 'www.' + target_host}
                    or parts.port not in {None, 80, 443} or parts.username or parts.password):
                return None
            decoded = parts.path
            for _ in range(3):
                more = unquote(decoded)
                if decoded == more:
                    break
                decoded = more
            if unquote(decoded) != decoded:
                return None
            if re.search(r'[\x00-\x1f\\]', decoded) or any(x == '..' for x in decoded.split('/')):
                return None
            if re.fullmatch(r'[\w.+-]+@[\w.-]+\.[a-zA-Z]{2,}',posixpath.basename(decoded.rstrip('/'))):
                return None
            path = posixpath.normpath('/' + decoded.lstrip('/'))
            if decoded.endswith('/') and path != '/':
                path += '/'
            if path.endswith('/index.php'):
                path = path[:-9]
            # Assets are only used by the optional browser; private API requests
            # are always prohibited, including same-origin JavaScript requests.
            if PRIVATE_PATH.search(path) and not (asset and path.startswith('/bitrix/') and path.lower().endswith(('.js', '.css'))):
                return None
            pairs = parse_qsl(parts.query, keep_blank_values=True)
            if len(pairs) > 12 or any(k.casefold() in {'action', 'logout', 'login', 'auth', 'sessid', 'token', 'password'} for k, _ in pairs):
                return None
            query = []
            for key, val in pairs:
                if QUERY_KEY.fullmatch(key):
                    if len(val) > 100 or not re.fullmatch(r'[\w.\-]+', val):
                        return None
                    if (key.casefold() in {'page', 'p'} or key.upper().startswith('PAGEN_')) and (not val.isdigit() or not 1 <= int(val) <= 1000):
                        return None
                    query.append((key, val))
            suffix = posixpath.splitext(path)[1].lower()
            if suffix in BLOCKED_SUFFIXES and not asset:
                return None
            return urlunsplit(('https', target_host, quote(path, safe='/%:@-._~'), urlencode(sorted(set(query))), ''))
        except (ValueError, TypeError):
            return None

    def check_public_dns(self):
        addresses = socket.getaddrinfo(urlsplit(self.origin).hostname, 443, type=socket.SOCK_STREAM)
        if not addresses or any(not ipaddress.ip_address(row[4][0]).is_global for row in addresses):
            raise ValueError('Crawl origin resolves to a non-public address')


def document_suffix(url):
    suffix = posixpath.splitext(unquote(urlsplit(url).path))[1].lower()
    return suffix if suffix in DOCUMENT_SUFFIXES else ''
