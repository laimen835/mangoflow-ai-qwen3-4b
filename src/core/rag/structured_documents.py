"""Local, provenance-preserving extraction and Nomic-token-limited chunks."""
from __future__ import annotations
from dataclasses import dataclass,asdict
from io import BytesIO
from pathlib import Path
import re
from bs4 import BeautifulSoup
from src.core.rag.chunker import TextChunk

EXTRACTION_VERSION='scholarship-extract-v1'
CHUNK_VERSION='scholarship-sections-v2'
OFFICE_EXTRACTION_VERSION='docx-smarttags-v2'


@dataclass
class Block:
    text:str
    section:str=''
    page:int|None=None
    sheet:str|None=None
    kind:str='text'


def html_blocks(body):
    soup=BeautifulSoup(body,'html.parser')
    root=soup.select_one('#ncsa-main .content_container') or soup.select_one('main')
    if root is None:raise ValueError('Main content not found')
    for el in root.select('nav,script,style,footer,.breadcrumb,.breadcrumbs'):el.decompose()
    blocks=[];section='Стипендии и меры поддержки'
    for node in root.find_all(['h1','h2','h3','h4','p','li','table']):
        if node.find_parent(['table','li']):continue
        if node.name.startswith('h'):section=node.get_text(' ',strip=True);continue
        if node.name=='table':
            rows=[[c.get_text(' ',strip=True) for c in row.find_all(['td','th'])] for row in node.find_all('tr')]
            if rows:
                for row in rows[1:]:blocks.append(Block(' | '.join(f'{h}: {v}' for h,v in zip(rows[0],row)),section,kind='table'))
        else:
            text=node.get_text(' ',strip=True)
            if text:blocks.append(Block(text,section))
    if not blocks:raise ValueError('No substantive blocks')
    return blocks


def office_blocks(body,suffix):
    import zipfile
    # Avoid decompressing unbounded ZIP office files.
    with zipfile.ZipFile(BytesIO(body)) as z:
        if sum(i.file_size for i in z.infolist())>100*1024*1024:raise ValueError('Expanded Office file exceeds 100 MB')
    if suffix=='.docx':
        from docx import Document
        from docx.table import Table
        from docx.text.paragraph import Paragraph
        doc=Document(BytesIO(body));blocks=[];section='Документ'
        # Word 2003 smart tags are transparent inline wrappers. python-docx
        # skips their runs, which can silently turn "3 г. 10 мес." into
        # ". 10 мес.". Unwrap visible children without importing tag metadata.
        from docx.oxml.ns import qn
        for node in list(doc.element.iter(qn('w:smartTag'))):
            parent=node.getparent()
            if parent is None:continue
            position=parent.index(node)
            for child in list(node):
                if child.tag==qn('w:smartTagPr'):continue
                parent.insert(position,child);position+=1
            parent.remove(node)
        for child in doc.element.body:
            if child.tag.endswith('}p'):
                p=Paragraph(child,doc);text=p.text.strip()
                if p.style and p.style.name.startswith('Heading'):section=text
                elif text:blocks.append(Block(text,section))
            elif child.tag.endswith('}tbl'):
                rows=[[c.text.strip() for c in row.cells] for row in Table(child,doc).rows]
                if rows:
                    for row in rows[1:]:blocks.append(Block(' | '.join(f'{h}: {v}' for h,v in zip(rows[0],row)),section,kind='table'))
        return blocks
    if suffix=='.xlsx':
        from openpyxl import load_workbook
        book=load_workbook(BytesIO(body),read_only=True,data_only=True)
        formulas=load_workbook(BytesIO(body),read_only=True,data_only=False)
        try:
            blocks=[]
            for sheet in book:
                for raw_row,cached_row in zip(formulas[sheet.title].iter_rows(),sheet.iter_rows()):
                    if any(raw.data_type=='f' and cached.value is None for raw,cached in zip(raw_row,cached_row)):
                        raise ValueError('Workbook formulas require recalculation before extraction')
                rows=iter(sheet.iter_rows(values_only=True));headers=next(rows,())
                for row in rows:
                    if not any(v is not None for v in row):continue
                    blocks.append(Block(' | '.join(f'{h or "Столбец "+str(i+1)}: {"" if v is None else v}' for i,(h,v) in enumerate(zip(headers,row))),sheet.title,sheet=sheet.title,kind='table'))
                    if len(blocks)>50000:raise ValueError('Workbook exceeds 50000 rows')
            return blocks
        finally:book.close();formulas.close()
    raise ValueError('Legacy DOC/XLS requires conversion; not HTML')


def pdf_blocks(body):
    import pymupdf
    blocks=[];chars=0
    with pymupdf.open(stream=body,filetype='pdf') as pdf:
        if len(pdf)>150:raise ValueError('PDF exceeds 150 pages')
        for i,page in enumerate(pdf):
            text=page.get_text(sort=True).strip();chars+=len(text)
            if text:blocks.append(Block(text,f'Страница {i+1}',page=i+1))
        if chars<max(100,len(pdf)*30):return [],'ocr_required'
    return blocks,'pymupdf'


def extract(body,suffix):
    if len(body)>20*1024*1024:raise ValueError('Document exceeds 20 MB')
    suffix=suffix.lower()
    if suffix=='.html':return html_blocks(body),'html'
    if suffix=='.pdf':return pdf_blocks(body)
    if suffix in {'.doc','.xls'}:raise ValueError('Legacy DOC/XLS requires conversion; not HTML')
    if suffix in {'.docx','.xlsx'}:
        return office_blocks(body,suffix),OFFICE_EXTRACTION_VERSION if suffix=='.docx' else suffix[1:]
    raise ValueError('Unsupported document type')


def render_blocks(blocks):
    return '\n\n'.join(f'Раздел: {b.section}\n{b.text}' for b in blocks)


def structural_chunks(blocks,title,tokenizer,*,max_tokens=384):
    result=[]
    def shorten(text,budget):
        encoded=tokenizer.encode(text)
        if len(encoded.ids)<=budget:return text
        offsets=[o for o in encoded.offsets[:budget-2] if o[1]>o[0]]
        return text[:offsets[-1][1]]+'…' if offsets else ''
    expanded=[]
    for block in blocks:
        # Keep list items with their introductory clause instead of arbitrary
        # cuts across the list (e.g. family support and its separate deadlines).
        parts=re.split(r'\n(?=\s*[—–-]\s|финансовая поддержка)',block.text)
        if len(parts)>1:
            intro=parts[0]
            expanded.extend(Block(intro+'\n'+part,block.section,block.page,block.sheet,block.kind) for part in parts[1:])
        else:expanded.append(block)
    for block in expanded:
        prefix=f'{shorten(title,64)}\nРаздел: {shorten(block.section,36)}\n'
        prefix_tokens=len(tokenizer.encode(prefix).ids)
        if prefix_tokens>=max_tokens-32:raise ValueError('Heading exceeds safe token budget')
        # Character spans come from the exact tokenizer offsets; never decode IDs
        # into replacement Unicode characters and never lose the section prefix.
        text=re.sub(r'\s+',' ',block.text).strip()
        encoded=tokenizer.encode(text);budget=max_tokens-prefix_tokens-8
        offsets=encoded.offsets
        for start in range(0,len(offsets),budget):
            window=offsets[start:start+budget]
            nonempty=[(a,b) for a,b in window if b>a]
            if not nonempty:continue
            a,b=nonempty[0][0],nonempty[-1][1]
            content=prefix+text[a:b]
            if len(tokenizer.encode(content).ids)>max_tokens:raise ValueError('Token limit exceeded')
            result.append(TextChunk(content,len(result),{k:v for k,v in {**asdict(block),'extraction_version':EXTRACTION_VERSION,'chunking_version':CHUNK_VERSION,'token_count':len(tokenizer.encode(content).ids)}.items() if k!='text'}))
    return result
