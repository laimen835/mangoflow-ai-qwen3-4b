"""Full-text and structural extraction; no source instructions are executed."""
from __future__ import annotations

from dataclasses import asdict
from io import BytesIO
import re
from pathlib import Path
import subprocess
import tempfile
import zipfile

from bs4 import BeautifulSoup, NavigableString, Comment
from src.core.rag.structured_documents import Block, office_blocks, structural_chunks

VERSION = 'knowledge-structure-v4'


def needs_source_review(title,url,extraction):
    return bool(extraction.get('ocr') or extraction.get('layout_review_required') or
        re.search(r'стипенди|материальн\w*\s+(?:помощ|поддерж)',title+' '+url,re.I))


def html_content(body, url, policy):
    soup = BeautifulSoup(body, 'html.parser')
    root = soup.select_one('#ncsa-main .content_container') or soup.select_one('main') or soup.select_one('#ncsa-main')
    if root is None:
        raise ValueError('Main page content not found')
    heading = root.find('h1') or soup.title
    title = heading.get_text(' ', strip=True) if heading else url
    if not title.strip():
        from src.core.services.academy_scope import unit_for_url
        unit = unit_for_url(url)
        title = unit.name if unit else url
    if title.strip().casefold() in {'карта сайта', 'страница не найдена', '404'}:
        raise ValueError('Soft 404')
    links = []
    # Discover the navigation, attachments, iframes and explicit lazy-load URLs.
    # Fetch boundaries apply again before every request, including redirects.
    for element in soup.select('a[href],iframe[src],embed[src],object[data],[data-url],[data-src]'):
        raw = next((element.get(k) for k in ('href', 'src', 'data', 'data-url', 'data-src') if element.get(k)), '')
        target = policy.canonical(raw, url)
        if target:
            links.append((target, element.get_text(' ', strip=True)[:300]))
    dates = {}
    for prop, key in [('article:published_time', 'published_at'), ('article:modified_time', 'source_updated_at')]:
        el = soup.find('meta', attrs={'property': prop})
        dates[key] = el.get('content') if el else None
    for el in root.select('script,style,nav,footer,noscript,.breadcrumb,.breadcrumbs,button,form'):
        el.decompose()
    for anchor in root.select('a[href]'):
        if anchor['href'].startswith('tel:'):
            anchor.insert_before('Телефон: ')
        elif anchor['href'].startswith('mailto:'):
            anchor.insert_before('Электронная почта: ')
        else:
            target = policy.canonical(anchor['href'], url)
            if target and not anchor.find_parent(['h1','h2','h3','h4','h5','h6']):
                anchor.append(' ('+target+')')
    blocks = []
    section = title

    def append_text(text, kind='text'):
        nonlocal section
        if re.match(r'Образовательная программа\s+\d{2}\.\d{2}\.\d{2}', text, re.I):
            section = text[:500]
        blocks.append(Block(text, section, kind=kind))

    def walk(node):
        nonlocal section
        if isinstance(node, Comment):
            return
        if isinstance(node, NavigableString):
            if node.strip():
                append_text(str(node).strip())
            return
        if 'contact-card' in (node.get('class') or []):
            text=node.get_text(' ',strip=True)
            if text:
                blocks.append(Block(text,section,kind='contact'))
            return
        if node.name in {'h1', 'h2', 'h3', 'h4', 'h5', 'h6'}:
            section = node.get_text(' ', strip=True)
            if section:
                blocks.append(Block(section, section, kind='heading'))
            return
        if node.name == 'table':
            cells = [row.find_all(['td', 'th'], recursive=False) for row in node.find_all('tr')]
            rows = [[c.get_text(' ', strip=True) for c in row] for row in cells]
            if rows:
                # A flat zip of a merged/multi-level header invents associations
                # between figures and programmes. Preserve those rows in the
                # full-text sidecar, but do not send them to the answer model.
                complex_layout = (node.find('table') is not None or
                    len({len(row) for row in rows}) != 1 or
                    any(c.get(key, '1') != '1' for row in cells for c in row for key in ('rowspan','colspan')) or
                    any(c.name == 'th' for row in cells[1:] for c in row))
                explicit_header = bool(cells[0]) and all(c.name == 'th' for c in cells[0])
                if explicit_header and (not all(rows[0]) or len(set(rows[0])) != len(rows[0])):
                    complex_layout = True
                if complex_layout:
                    for index,row in enumerate(rows,1):
                        blocks.append(Block(f'Непроверенная структура таблицы, строка {index}: '+ ' | '.join(row),section,kind='unverified_table'))
                else:
                    headers = rows[0] if explicit_header else [f'Колонка {i+1}' for i in range(len(rows[0]))]
                    if explicit_header:
                        blocks.append(Block(' | '.join(headers), section, kind='table_header'))
                    for index,row in enumerate(rows[1:] if explicit_header else rows,2 if explicit_header else 1):
                        text = ' | '.join(f'{headers[i]}: {value}' for i,value in enumerate(row))
                        blocks.append(Block(f'Строка {index}: {text}', section, kind='table'))
            return
        if node.name in {'p', 'li', 'dt', 'dd'} and not node.find(['p', 'li', 'table']):
            text = node.get_text(' ', strip=True)
            if text:
                append_text(text)
            return
        for child in node.children:
            walk(child)

    walk(root)
    # Merge adjacent small inline fragments, never cross a section or table row.
    merged = []
    for block in blocks:
        if (merged and block.kind == merged[-1].kind == 'text' and block.section == merged[-1].section
                and len(merged[-1].text) + len(block.text) < 600):
            merged[-1].text += '\n' + block.text
        else:
            merged.append(block)
    return title, merged, list(dict.fromkeys(links)), dates


def office_zip(body):
    with zipfile.ZipFile(BytesIO(body)) as archive:
        if len(archive.infolist()) > 20000 or sum(i.file_size for i in archive.infolist()) > 100 * 1024 * 1024:
            raise ValueError('Expanded Office archive exceeds limits')


def pptx_blocks(body):
    from lxml import etree
    office_zip(body)
    blocks = []
    parser = etree.XMLParser(resolve_entities=False, no_network=True)
    with zipfile.ZipFile(BytesIO(body)) as archive:
        names = sorted((n for n in archive.namelist() if re.fullmatch(r'ppt/slides/slide\d+\.xml', n)), key=lambda n: int(re.search(r'(\d+)\.xml', n)[1]))
        if 'ppt/presentation.xml' in archive.namelist() and 'ppt/_rels/presentation.xml.rels' in archive.namelist():
            presentation=etree.fromstring(archive.read('ppt/presentation.xml'),parser)
            relationships=etree.fromstring(archive.read('ppt/_rels/presentation.xml.rels'),parser)
            targets={r.get('Id'):'ppt/'+r.get('Target','') for r in relationships if r.get('TargetMode')!='External'}
            ordered=[targets.get(s.get('{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id'))
                     for s in presentation.findall('.//{http://schemas.openxmlformats.org/presentationml/2006/main}sldId')]
            if any(n not in names for n in ordered):
                raise ValueError('Invalid slide order relationships')
            names=ordered
        for number, name in enumerate(names, 1):
            root = etree.fromstring(archive.read(name), parser)
            # DrawingML paragraphs cover both text boxes and table cells.
            for p in root.findall('.//{http://schemas.openxmlformats.org/drawingml/2006/main}p'):
                text = ' '.join(p.itertext()).strip()
                if text:
                    blocks.append(Block(text, f'Слайд {number}', page=number))
    return blocks


def convert_legacy(body, suffix, executable):
    if not executable or not Path(executable).is_file():
        raise ValueError('Legacy Office conversion requires a configured LibreOffice executable')
    target = {'.doc': 'docx', '.xls': 'xlsx', '.ppt': 'pptx'}[suffix]
    with tempfile.TemporaryDirectory(prefix='mangoflow-office-') as folder:
        root = Path(folder)
        source = root / ('source' + suffix)
        source.write_bytes(body)
        profile = (root / 'profile').as_uri()
        run = subprocess.run([str(executable), '-env:UserInstallation=' + profile, '--headless', '--convert-to', target,
                              '--outdir', str(root), str(source)], capture_output=True, timeout=90,
                             creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        output = root / ('source.' + target)
        if run.returncode or not output.is_file() or output.stat().st_size > 20 * 1024 * 1024:
            raise ValueError('Legacy Office conversion failed')
        return output.read_bytes(), '.' + target


def file_content(body, suffix, *, tesseract, cache, ocr_pages=30, office_executable=''):
    original_suffix = suffix
    if suffix == '.xls' and not office_executable:
        import xlrd
        book = xlrd.open_workbook(file_contents=body, on_demand=True)
        blocks = []
        try:
            for sheet in book.sheets():
                if sheet.nrows > 50000 or sheet.ncols > 512:
                    raise ValueError('Legacy spreadsheet exceeds row/column limits')
                for index in range(sheet.nrows):
                    values = []
                    for cell in sheet.row(index):
                        if cell.ctype == xlrd.XL_CELL_DATE:
                            value = xlrd.xldate.xldate_as_datetime(cell.value, book.datemode).isoformat()
                        else:
                            value = str(cell.value)
                        values.append(value)
                    blocks.append(Block('Строка '+str(index+1)+': '+' | '.join(values), sheet.name, sheet=sheet.name, kind='table'))
        finally:
            book.release_resources()
        # BIFF formula provenance and merged headers require a separate review.
        return blocks, {'extraction':'xlrd-biff', 'ocr':False, 'layout_review_required':True}
    if suffix in {'.doc', '.xls', '.ppt'}:
        body, suffix = convert_legacy(body, suffix, office_executable)
    if suffix in {'.docx', '.xlsx'}:
        blocks = office_blocks(body, suffix)
        return blocks, {'extraction': suffix[1:] if suffix == original_suffix else 'libreoffice-' + suffix[1:], 'ocr': False}
    if suffix == '.pptx':
        return pptx_blocks(body), {'extraction': 'drawingml', 'ocr': False}
    if suffix != '.pdf' or not body.startswith(b'%PDF'):
        raise ValueError('Unsupported or invalid document signature')
    import pymupdf
    with pymupdf.open(stream=body, filetype='pdf') as pdf:
        if len(pdf) > 150:
            raise ValueError('PDF exceeds 150 pages')
        pages = [page.get_text(sort=True).strip() for page in pdf]
        sparse = [i for i, text in enumerate(pages) if len(text) < 50]
        tables=[]
        table_errors=[]
        for i,page in enumerate(pdf):
            if i in sparse:
                continue
            try:
                for table in page.find_tables().tables:
                    for row_index,row in enumerate(table.extract(),1):
                        text=' | '.join('' if value is None else str(value) for value in row)
                        tables.append(Block(f'Строка {row_index}: {text}',f'Таблица, страница {i+1}',page=i+1,kind='table'))
            except Exception:
                table_errors.append(i+1)
        file_dates={key:pdf.metadata.get(key) for key in ('creationDate','modDate')}
    ocr = bool(sparse)
    if sparse:
        from src.core.rag.ocr import recognize_pdf
        recognized = recognize_pdf(body, tesseract, Path(cache), max_pages=ocr_pages)
        if len(recognized) != len(pages):
            raise ValueError('Incomplete OCR page count')
        for i in sparse:
            pages[i] = recognized[i]
    blocks = [Block(text, f'Страница {i+1}', page=i+1) for i, text in enumerate(pages) if text]
    blocks.extend(tables)
    missing_pages = [i+1 for i, text in enumerate(pages) if not text.strip()]
    if missing_pages:
        raise ValueError('Unreadable PDF pages: ' + ','.join(map(str, missing_pages)))
    return blocks, {'extraction': 'pymupdf+ocr' if ocr else 'pymupdf', 'ocr': ocr, 'pages': len(pages),
                    'file_dates':file_dates, 'table_rows':len(tables), 'table_extraction_error_pages':table_errors}


def full_text(blocks):
    return '\n\n'.join(f'Раздел: {b.section}\n{b.text}' for b in blocks)


def semantic_chunks(blocks, title, tokenizer):
    # Keep paragraph/list/table boundaries; group only within the same section,
    # page and sheet. Split oversized blocks by sentences before token windows.
    packed = []
    for index,block in enumerate(blocks):
        if block.kind == 'unverified_table':
            continue
        if block.kind=='heading' and index+1<len(blocks) and blocks[index+1].section==block.section and blocks[index+1].kind!='heading':
            # The following block already carries this exact heading in its
            # prefix. A heading by itself is not evidence for the requested fact.
            continue
        if block.kind=='heading' and not (re.search(r'\d',block.text) or len(block.text.split())>=12):
            continue
        sentences = re.split(r'(?<=[.!?;])\s+(?=[А-ЯЁA-Z0-9])', block.text) if block.kind == 'text' else [block.text]
        for sentence in sentences:
            if not sentence.strip():
                continue
            current = Block(sentence, block.section, block.page, block.sheet, block.kind)
            if (packed and current.kind == packed[-1].kind == 'text'
                    and (current.section, current.page, current.sheet) == (packed[-1].section, packed[-1].page, packed[-1].sheet)
                    and len(tokenizer.encode(packed[-1].text + '\n' + sentence).ids) < 260):
                packed[-1].text += '\n' + sentence
            else:
                packed.append(current)
    chunks = structural_chunks(packed, title, tokenizer, max_tokens=384)
    for chunk in chunks:
        chunk.meta.update(extraction_version=VERSION,chunking_version=VERSION)
    return chunks
