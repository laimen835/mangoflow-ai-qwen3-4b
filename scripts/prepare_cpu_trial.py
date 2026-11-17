"""Export the working checkout, consistent DB and exact active model files.

Nothing is uploaded. Artifacts contain private database data and stay on D.
Environment files, passwords, logs, ngrok and unrelated models are excluded.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tarfile
import urllib.request
from datetime import datetime, timezone
from sqlalchemy.engine import make_url
from src.core.config import settings

ROOT = Path(__file__).resolve().parents[1]


def add_tree(archive, source, destination):
    source = Path(source).resolve()
    for path in sorted(source.rglob('*')):
        if not path.is_file() or path.is_symlink():
            continue
        relative = path.relative_to(source)
        if any(p in {'__pycache__', '.pytest_cache', '.git', 'state'} for p in relative.parts):
            continue
        if path.name.startswith('.env') or path.name=='trial.env' or path.suffix in {'.pyc', '.key', '.pem'}:
            continue
        archive.add(path, arcname=str(Path(destination) / relative).replace('\\', '/'), recursive=False)


def active_models(root):
    records = []
    blobs = {}
    with urllib.request.urlopen(settings.ollama_base_url.rstrip('/')+'/api/tags',timeout=10) as response:
        running = {m['name']:m['digest'] for m in json.load(response)['models']}
    for name in (settings.llm_model, settings.embedding_model):
        model, _, tag = name.partition(':')
        if not model or '/' in model or '\\' in model:
            raise ValueError('Review non-library model paths before export')
        canonical = model + ':' + (tag or 'latest')
        expected = running[canonical]
        candidates = [Path('manifests-v2/ollama.com/library') / model / (tag or 'latest'),
                      Path('manifests/registry.ollama.ai/library') / model / (tag or 'latest')]
        manifest = next((p for p in candidates if (root/p).is_file() and
                         hashlib.sha256((root/p).read_bytes()).hexdigest()==expected),None)
        if manifest is None:
            raise ValueError('Stored manifest does not match the model used by the running API')
        body = (root / manifest).read_bytes()
        digest = hashlib.sha256(body).hexdigest()
        parsed = json.loads(body)
        for layer in [parsed['config'], *parsed['layers']]:
            value = layer['digest']
            if not value.startswith('sha256:') or len(value) != 71:
                raise ValueError('Unexpected model layer digest')
            relative = Path('blobs') / value.replace(':', '-')
            blob = root / relative
            if blob.stat().st_size != layer['size']:
                raise ValueError('Model file size mismatch')
            blobs[relative] = blob
        records.append({'name':canonical,'digest':'sha256:'+digest,'manifest':manifest.as_posix()})
    return records, blobs


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    output = Path(args.output).resolve()
    if ROOT == output or ROOT in output.parents:
        raise ValueError('Keep private export outside the repository')
    output.mkdir(parents=True, exist_ok=False)
    model_root = Path('D:/Codex-RAG-runtime/models')
    records, blobs = active_models(model_root)
    with tarfile.open(output / 'code.tar.gz', 'w:gz') as archive:
        for directory in ('src','widget','migrations','deploy/cpu-trial'):
            add_tree(archive, ROOT / directory, directory)
        archive.add(ROOT/'scripts/benchmark_cpu_trial.py',arcname='scripts/benchmark_cpu_trial.py')
        for filename in ('requirements.txt','requirements-local.txt','alembic.ini','.dockerignore'):
            archive.add(ROOT / filename, arcname=filename)
    with tarfile.open(output / 'data.tar.gz', 'w:gz') as archive:
        for source, destination in ((settings.raw_dir, 'raw'), (settings.storage_dir, 'storage')):
            add_tree(archive, Path(source), destination)
    with tarfile.open(output / 'models.tar', 'w') as archive:
        for record in records:
            path = record['manifest']
            archive.add(model_root / path, arcname='models/' + path)
        for relative, path in sorted(blobs.items()):
            archive.add(path, arcname='models/' + relative.as_posix())
    url = make_url(settings.database_url)
    subprocess.run(['D:/Codex-RAG-runtime/pgsql/bin/pg_dump.exe', '-h',url.host,'-p',str(url.port),
        '-U',url.username,'-d',url.database,'-Fc','-f',str(output/'database.dump')],
        env={**os.environ,'PGPASSWORD':url.password},check=True,capture_output=True)
    files = {}
    for path in output.iterdir():
        digest = hashlib.sha256()
        with path.open('rb') as stream:
            for block in iter(lambda: stream.read(8*1024*1024), b''):
                digest.update(block)
        files[path.name] = {'bytes':path.stat().st_size,'sha256':digest.hexdigest()}
    manifest = {'created_at':datetime.now(timezone.utc).isoformat(), 'models':records,'files':files,
        'privacy':'Private database export. Do not publish or add to Git.',
        'state':'Prepared locally; Linux image build, restore and cloud benchmark still required.'}
    (output/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'output':str(output),'files':files,'models':records},ensure_ascii=False))


if __name__ == '__main__':
    main()
