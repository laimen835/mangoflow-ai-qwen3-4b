"""Ingest frozen grants files. Human reviews are bound to exact source hashes."""
import hashlib,json,re
from pathlib import Path
from src.core.rag.ingest import ingest_text
from src.core.rag.structured_documents import Block,structural_chunks,render_blocks,EXTRACTION_VERSION,CHUNK_VERSION
from src.core.rag.chunker import RecursiveChunker


def reviewed_blocks(extracted,review):
    pages={b['page']:b['text'] for b in extracted['blocks']}
    result=[]
    for span in review.get('approved_ranges',[]):
        text=pages[span['page']]
        start=text.index(span['start']);end=text.index(span['end'],start)+len(span['end'])
        text=text[start:end]
        for old,new in review.get('corrections',{}).items():text=text.replace(old,new)
        # Numbered clauses remain intact; chunks carry their own section and page.
        parts=re.split(r'(?m)(?=^\s*\d+\.\d+\s*\.)',text)
        for part in parts:
            if not part.strip():continue
            match=re.match(r'\s*(\d+\.\d+)',part)
            section=f'Порядок материальной поддержки, пункт {match.group(1)}' if match else span['section']
            result.append(Block(part.strip(),section,page=span['page']))
    return result


def load_materials(snapshot,reviews_path):
    snapshot=Path(snapshot);manifest=json.loads((snapshot/'manifest.json').read_text(encoding='utf-8'))
    reviews=json.loads(Path(reviews_path).read_text(encoding='utf-8'))
    materials=[]
    for item in manifest['items']:
        source=(snapshot/item['file']).resolve()
        if source.parent!=snapshot.resolve():raise ValueError('Snapshot path outside root')
        if hashlib.sha256(source.read_bytes()).hexdigest()!=item['sha256']:raise ValueError('Source hash changed')
        review=reviews.get(item['sha256'],{})
        extracted_path=source.with_suffix(source.suffix+'.extracted.json')
        extracted=json.loads(extracted_path.read_text(encoding='utf-8'))
        if review.get('text_verified'):
            blocks=reviewed_blocks(extracted,review)
            proof=review.get('scope_quote','')
            normalized=lambda t:re.sub(r'\s+',' ',t).strip().casefold()
            if not proof or normalized(proof) not in normalized(render_blocks(blocks)) or not re.search(r'СПК|среднего профессионального',proof,re.I):
                raise ValueError('Review has no scope evidence in the extracted text')
        else:blocks=[Block(**b) for b in extracted.get('blocks',[])]
        # Failed/unsupported extraction remains an administrative source record.
        if not blocks:blocks=[Block('Извлечение не завершено: '+extracted.get('error','требуется проверка'),'Ошибка обработки')]
        materials.append((item,blocks,extracted,review))
    return manifest,materials


async def ingest_snapshot(db,embedder,settings,*,snapshot,reviews_path,variant,tokenizer):
    manifest,materials=load_materials(snapshot,reviews_path);results=[];errors=list(manifest['errors'])
    tokenizer_digest=hashlib.sha256(tokenizer.to_str().encode()).hexdigest()[:16]
    for item,blocks,extracted,review in materials:
        text=render_blocks(blocks)
        if extracted.get('error'):errors.append({'url':item['url'],'reason':extracted['error']})
        if variant=='structured':chunks=structural_chunks(blocks,item['title'],tokenizer)
        else:
            chunks=RecursiveChunker(settings.chunk_size,settings.chunk_overlap).split(text,{'title':item['title']})
            for chunk in chunks:
                pages=[b.page for b in blocks if b.page is not None and re.sub(r'\s+',' ',b.text).strip()[:60] in re.sub(r'\s+',' ',chunk.content)]
                chunk.meta.update(pages=pages,extraction_version=EXTRACTION_VERSION)
        meta={'scholarship_material':True,'source':'ncsa_grants','source_sha256':item['sha256'],
              'linked_from':[item['parent']],'fetched_at':manifest['fetched_at'],'extraction':extracted['method'],
              'extraction_version':EXTRACTION_VERSION,'review':review,'extraction_error':extracted.get('error'),
              'extraction_seconds':extracted.get('seconds'),'source_snapshot':item['file']}
        result=await ingest_text(db,title=item['title'],text=text,source_type='ncsa_scholarship',
            source_url=item['url'],external_id=item['url'],meta=meta,embedder=embedder,settings=settings,
            prepared_chunks=chunks,processing_version=f'{EXTRACTION_VERSION}:{extracted["method"]}:{CHUNK_VERSION if variant=="structured" else "recursive-v2"}:{settings.chunk_size}:{settings.chunk_overlap}:{tokenizer_digest}')
        results.append({'url':item['url'],'sha256':item['sha256'],'reviewed':bool(review.get('text_verified')),**result})
    return {'documents':results,'errors':errors,'fetched_at':manifest['fetched_at'],'variant':variant}
