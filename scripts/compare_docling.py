"""Run in the isolated D: Docling environment, not the API environment."""
import os,json,time
from pathlib import Path
ROOT=Path('D:/Codex-RAG-runtime/scholarship-pilot')
os.environ['HF_HOME']=str(ROOT/'docling-models/huggingface')
os.environ['TORCH_HOME']=str(ROOT/'docling-models/torch')
os.environ['OMP_NUM_THREADS']='2'
from docling.document_converter import DocumentConverter,PdfFormatOption
from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import PdfPipelineOptions,RapidOcrOptions
from docling.datamodel.accelerator_options import AcceleratorOptions


def main():
    options=PdfPipelineOptions(do_ocr=True,do_table_structure=True,
        ocr_options=RapidOcrOptions(lang=['cyrillic'],backend='onnxruntime'),
        accelerator_options=AcceleratorOptions(device='cpu',num_threads=2))
    converter=DocumentConverter(format_options={InputFormat.PDF:PdfFormatOption(pipeline_options=options)})
    out=ROOT/'docling';out.mkdir(exist_ok=True)
    for filename in ['24d1af547494f15b.pdf','f748cce1ee563259.pdf','554478bc7c3d6082.pdf']:
        target=out/(filename+'.json')
        if target.exists():continue
        started=time.monotonic()
        try:
            r=converter.convert(ROOT/'snapshot-v2'/filename)
            r.document.save_as_json(out/(filename+'.document.json'))
            text=r.document.export_to_markdown()
            blocks=[]
            for item,level in r.document.iterate_items():
                page=item.prov[0].page_no if item.prov else None
                if hasattr(item,'text') and item.text:blocks.append({'page':page,'text':item.text,'label':str(item.label)})
            d={'filename':filename,'status':str(r.status),'text':text,'blocks':blocks,'seconds':round(time.monotonic()-started,2)}
        except Exception as exc:d={'filename':filename,'error':str(exc),'seconds':round(time.monotonic()-started,2)}
        target.write_text(json.dumps(d,ensure_ascii=False,indent=2),encoding='utf-8')
        print(filename,d.get('status',d.get('error')),d['seconds'],flush=True)


if __name__=='__main__':main()
