"""Local, CPU-only ONNX cross-encoder. No network calls during inference."""
import asyncio
from functools import lru_cache
import hashlib
import json
from pathlib import Path
import threading


class LocalReranker:
    def __init__(self,directory):
        import numpy as np
        import onnxruntime as ort
        from tokenizers import Tokenizer
        self.np=np
        directory=Path(directory)
        manifest=json.loads((directory/'manifest.json').read_text(encoding='utf-8'))
        for filename,digest in manifest['files'].items():
            if hashlib.sha256((directory/filename).read_bytes()).hexdigest()!=digest:
                raise ValueError('Reranker file checksum mismatch')
        self.tokenizer=Tokenizer.from_file(str(directory/'tokenizer.json'))
        self.tokenizer.enable_truncation(max_length=int(manifest.get('max_length',512)))
        config=json.loads((directory/'config.json').read_text(encoding='utf-8'))
        pad_id=config.get('pad_token_id',0)
        self.tokenizer.enable_padding(pad_id=pad_id,pad_token=self.tokenizer.id_to_token(pad_id))
        options=ort.SessionOptions();options.intra_op_num_threads=3;options.inter_op_num_threads=1
        model_file=manifest.get('model_file','model_quint8_avx2.onnx')
        if Path(model_file).name!=model_file or model_file not in manifest['files']:
            raise ValueError('Invalid model filename in reranker manifest')
        self.session=ort.InferenceSession(str(directory/model_file),sess_options=options,
                                         providers=['CPUExecutionProvider'])
        self.inputs={i.name for i in self.session.get_inputs()}
        self.lock=threading.Lock()

    def predict(self,query,passages):
        if not passages:return []
        with self.lock:
            batch=self.tokenizer.encode_batch([(query,text) for text in passages])
            values={'input_ids':[e.ids for e in batch],'attention_mask':[e.attention_mask for e in batch],
                    'token_type_ids':[e.type_ids for e in batch]}
            feed={k:self.np.asarray(v,dtype=self.np.int64) for k,v in values.items() if k in self.inputs}
            return self.session.run(None,feed)[0].reshape(-1).astype(float).tolist()


@lru_cache(maxsize=2)
def load_reranker(directory):return LocalReranker(directory)


async def rerank(query,passages,directory):
    def run():return load_reranker(directory).predict(query,passages)
    return await asyncio.to_thread(run)
