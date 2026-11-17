"""Download pinned public ONNX/tokenizer files to D:, never during a question."""
import hashlib,json
from pathlib import Path
import httpx
from src.core.config import settings

REPOSITORY='cross-encoder/mmarco-mMiniLMv2-L12-H384-v1'
REVISION='1427fd652930e4ba29e8149678df786c240d8825'


def main():
    out=Path(settings.reranker_directory);out.mkdir(parents=True,exist_ok=True)
    names=['config.json','tokenizer.json','tokenizer_config.json','special_tokens_map.json','onnx/model_quint8_avx2.onnx']
    with httpx.Client(timeout=240,follow_redirects=True) as client:
        for name in names:
            path=out/Path(name).name
            if path.exists():continue
            with client.stream('GET',f'https://huggingface.co/{REPOSITORY}/resolve/{REVISION}/{name}') as response:
                response.raise_for_status();temporary=path.with_suffix(path.suffix+'.download')
                with temporary.open('wb') as stream:
                    for block in response.iter_bytes():stream.write(block)
                temporary.replace(path)
    manifest={'repository':REPOSITORY,'revision':REVISION,
              'files':{Path(n).name:hashlib.sha256((out/Path(n).name).read_bytes()).hexdigest() for n in names}}
    (out/'manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    print('Pinned local reranker ready:',out)


if __name__=='__main__':main()
