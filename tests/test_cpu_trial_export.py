import hashlib
import io
import json
from pathlib import Path
import tarfile
from types import SimpleNamespace
from scripts import prepare_cpu_trial as export


def test_code_archive_does_not_export_env_keys_or_python_cache(tmp_path):
    source=tmp_path/'source'
    source.mkdir()
    for name in ('.env','.env.local','trial.env','secret.pem','cached.pyc','app.py'):
        (source/name).write_text('fixture',encoding='utf-8')
    (source/'__pycache__').mkdir()
    (source/'__pycache__'/'cache.py').write_text('fixture',encoding='utf-8')
    target=tmp_path/'code.tar'
    with tarfile.open(target,'w') as archive:
        export.add_tree(archive,source,'src')
    with tarfile.open(target) as archive:
        assert archive.getnames()==['src/app.py']


def test_exact_running_v2_manifest_wins_over_stale_legacy(tmp_path,monkeypatch):
    payload=b'active model layer'
    layer=hashlib.sha256(payload).hexdigest()
    blob=tmp_path/'blobs'/('sha256-'+layer)
    blob.parent.mkdir();blob.write_bytes(payload)
    body=json.dumps({'config':{'digest':'sha256:'+layer,'size':len(payload)},'layers':[]}).encode()
    digest=hashlib.sha256(body).hexdigest()
    for prefix in ('manifests-v2/ollama.com/library','manifests/registry.ollama.ai/library'):
        path=tmp_path/prefix/'fixture'/'latest'
        path.parent.mkdir(parents=True)
        path.write_bytes(body if prefix.startswith('manifests-v2') else b'{}')
    monkeypatch.setattr(export,'settings',SimpleNamespace(llm_model='fixture',embedding_model='fixture',ollama_base_url='http://localhost'))
    monkeypatch.setattr(export.urllib.request,'urlopen',lambda *a,**kw:io.BytesIO(json.dumps(
        {'models':[{'name':'fixture:latest','digest':digest}]}).encode()))
    records, blobs=export.active_models(tmp_path)
    assert all(r['manifest'].startswith('manifests-v2/') and r['digest']=='sha256:'+digest for r in records)
    assert list(blobs.values())==[blob]
