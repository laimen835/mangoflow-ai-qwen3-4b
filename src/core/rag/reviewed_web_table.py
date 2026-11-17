"""Review a single frozen official table; never approve the rest of the page."""
import hashlib
import json
from bs4 import BeautifulSoup
from src.core.rag.scholarship_source import official_url

VERSION = 'reviewed-web-table-v1'


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(',', ':')).encode()).hexdigest()


def extract_reviewed_table(body, review):
    if hashlib.sha256(body).hexdigest() != review['source_sha256']:
        raise ValueError('Reviewed HTML hash mismatch')
    soup = BeautifulSoup(body, 'html.parser')
    root = soup.select_one('#ncsa-main .content_container') or soup.select_one('main')
    if root is None:
        raise ValueError('Official content container not found')
    matches = []
    for table in root.find_all('table'):
        rows = [[c.get_text(' ', strip=True) for c in row.find_all(['td', 'th'], recursive=False)]
                for row in table.find_all('tr')]
        if rows and rows[0] == review['headers'] and digest(rows) == review['table_sha256']:
            matches.append(rows)
    if len(matches) != 1:
        raise ValueError('Reviewed table changed or is ambiguous')
    rows = matches[0]
    selected = []
    for label in review['approved_rows']:
        matches = [r for r in rows[1:] if r and r[0] == label]
        if len(matches) != 1 or len(matches[0]) != len(rows[0]):
            raise ValueError('Reviewed row is missing or has merged cells')
        selected.append(dict(zip(rows[0], matches[0])))
    links = []
    for approved in review.get('approved_links', []):
        matches = [a for a in root.select('a[href]') if a.get_text(' ', strip=True) == approved['title']
                   and official_url(a['href'], review['source_url']) == approved['url']]
        if not matches:
            raise ValueError('Reviewed link is no longer in source')
        links.append(approved)
    return {'rows': selected, 'links': links}
