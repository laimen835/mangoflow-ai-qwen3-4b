"""Restore an explicit, verified export on a fresh Ubuntu trial VM (as root)."""
import hashlib
import json
from pathlib import Path
import secrets
import subprocess
import tarfile
import time
import urllib.request

BASE = Path('/opt/spk-cpu-trial')
TRANSFER = BASE / 'transfer'
APP = BASE / 'app'


def run(*args, **kwargs):
    subprocess.run(args, check=True, **kwargs)


def extract(name, destination):
    destination.mkdir(parents=True, exist_ok=True)
    with tarfile.open(TRANSFER / name) as archive:
        archive.extractall(destination, filter='data')


def main():
    manifest = json.loads((TRANSFER / 'manifest.json').read_text(encoding='utf-8-sig'))
    for name, record in manifest['files'].items():
        if name not in {'code.tar.gz', 'data.tar.gz', 'models.tar', 'database.dump'}:
            raise ValueError('Unexpected artifact')
        path = TRANSFER / name
        with path.open('rb') as stream:
            digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        if path.stat().st_size != record['bytes'] or digest != record['sha256']:
            raise ValueError('Artifact verification failed: ' + name)
    print('All artifact hashes verified', flush=True)
    extract('code.tar.gz', APP)
    deployment = APP / 'deploy' / 'cpu-trial'
    env = deployment / 'trial.env'
    if not env.exists():
        with env.open('x') as stream:
            stream.write('POSTGRES_PASSWORD=' + secrets.token_hex(32) + '\n')
            stream.write('ADMIN_API_KEY=' + secrets.token_hex(32) + '\n')
        env.chmod(0o600)
    state = deployment / 'state'
    if not (state / '.artifacts-restored').exists():
        extract('data.tar.gz', state / 'data')
        extract('models.tar', state / 'ollama')
        (state / 'logs').mkdir(exist_ok=True)
        run('chown', '-R', '10001:10001', str(state / 'data'), str(state / 'logs'))
        (state / '.artifacts-restored').touch()
    if not Path('/usr/bin/docker').exists():
        installer = BASE / 'get-docker.sh'
        urllib.request.urlretrieve('https://get.docker.com', installer)
        run('sh', str(installer))
    compose = ['docker', 'compose', '--env-file', str(env), '-f', str(deployment / 'compose.json')]
    run(*compose, 'up', '-d', 'db', 'ollama')
    for attempt in range(60):
        check = subprocess.run(compose + ['exec', '-T', 'db', 'pg_isready', '-U', 'postgres', '-d', 'rag'], capture_output=True)
        if check.returncode == 0:
            break
        time.sleep(2)
    else:
        raise RuntimeError('Database did not become ready')
    if not (state / '.database-restored').exists():
        existing = subprocess.check_output(compose + ['exec', '-T', 'db', 'psql', '-U', 'postgres', '-d', 'rag', '-Atc', "SELECT count(*) FROM information_schema.tables WHERE table_schema='public'"])
        if int(existing.strip()) != 0:
            raise RuntimeError('Refusing to restore over a non-empty database')
        with (TRANSFER / 'database.dump').open('rb') as stream:
            run(*compose, 'exec', '-T', 'db', 'pg_restore', '--exit-on-error', '--single-transaction', '--no-owner', '--no-privileges', '-U', 'postgres', '-d', 'rag', stdin=stream)
        (state / '.database-restored').touch()
    run(*compose, 'up', '-d', '--build', 'api')
    print('Containers started; readiness and answers require verification.', flush=True)


if __name__ == '__main__':
    main()
