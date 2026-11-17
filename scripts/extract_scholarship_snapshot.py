import json,time
from pathlib import Path
from dataclasses import asdict
from src.core.rag.structured_documents import extract,Block
from src.core.rag.ocr import recognize_pdf
from src.core.config import settings

def extract_file(path):
    started=time.monotonic()
    method='conversion_required' if path.suffix.lower() in {'.doc','.xls'} else 'extraction_failed'
    try:
        blocks,method=extract(path.read_bytes(),path.suffix)
        if method=='ocr_required':
            # Exceeding the existing OCR limit is an error, never truncation.
            pages=recognize_pdf(path.read_bytes(),settings.tesseract_cmd,path.parent/'ocr-cache')
            blocks=[Block(t,f'Страница {i+1}',page=i+1) for i,t in enumerate(pages) if t]
            method='windows_ocr'
        return {'method':method,'blocks':[asdict(b) for b in blocks],'seconds':round(time.monotonic()-started,2)}
    except Exception as exc:
        return {'method':method,'blocks':[],'error':str(exc),'seconds':round(time.monotonic()-started,2)}


def main():
    root=Path('D:/Codex-RAG-runtime/scholarship-pilot/snapshot-v2')
    manifest=json.loads((root/'manifest.json').read_text(encoding='utf-8'))
    for item in manifest['items']:
        path=root/item['file'];out=path.with_suffix(path.suffix+'.extracted.json')
        if out.exists():continue
        result=extract_file(path)
        out.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        print(item['file'],result['method'],len(result['blocks']),result.get('error',''),flush=True)


if __name__=='__main__':main()
