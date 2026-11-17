"""Owner-run transfer while the PC's VPN is disconnected. No secrets are printed."""
import argparse
from datetime import datetime, timezone
import hashlib
import ipaddress
import json
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import time

ARTIFACTS = ('code.tar.gz', 'data.tar.gz', 'database.dump', 'models.tar')


def main(config_path):
    config = json.loads(config_path.read_text(encoding='utf-8-sig'))
    address = str(ipaddress.ip_address(config['host']))
    folder = Path(config['export_folder']).resolve()
    private_key = folder / 'ssh' / 'spk_cpu_trial_rsa'
    known_hosts = folder / 'ssh' / 'known_hosts'
    if not private_key.is_file() or not known_hosts.is_file():
        raise RuntimeError('Missing local SSH key or previously verified known_hosts')
    manifest = json.loads((folder / 'manifest.json').read_text(encoding='utf-8-sig'))
    if set(manifest['files']) != set(ARTIFACTS):
        raise RuntimeError('Unexpected artifact list; refusing upload')
    result_path = folder / 'transfer-result.json'
    result = {'started_utc': datetime.now(timezone.utc).isoformat(), 'status': 'checking', 'server_id': config['server_id']}
    log = (folder / 'transfer.log').open('a', encoding='utf-8', buffering=1)

    def say(message):
        print(message, flush=True)
        log.write(message + '\n')

    options = ['-i', str(private_key), '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=15',
               '-o', 'StrictHostKeyChecking=yes', '-o', 'UserKnownHostsFile=' + known_hosts.as_posix(),
               '-o', 'ServerAliveInterval=15', '-o', 'ServerAliveCountMax=4']
    target = 'root@' + address

    def run(args, timeout=900, cwd=None, capture=False):
        if capture:
            return subprocess.check_output(args, cwd=cwd, timeout=timeout, stderr=subprocess.STDOUT, encoding='utf-8', errors='replace')
        # Keep verbose install output in a private local log.
        with (folder / 'transfer-command.log').open('ab') as output:
            subprocess.run(args, cwd=cwd, timeout=timeout, stdout=output, stderr=subprocess.STDOUT, check=True)

    def remote(command, **kwargs):
        return run(['ssh', *options, target, command], **kwargs)

    try:
        say('1/5 Checking SSH and the exact trial server...')
        machine = remote('hostname -s', capture=True, timeout=30).strip()
        if machine != 'cv' + str(int(config['server_id'])):
            raise RuntimeError('Unexpected server hostname: ' + machine)
        say('SSH OK: ' + machine)
        say('2/5 Verifying local archives (about 3 GB)...')
        for name in ARTIFACTS:
            path = folder / name
            with path.open('rb') as stream:
                digest = hashlib.file_digest(stream, 'sha256').hexdigest()
            if path.stat().st_size != manifest['files'][name]['bytes'] or digest != manifest['files'][name]['sha256']:
                raise RuntimeError('Archive integrity check failed: ' + name)
        bootstrap = Path(config['repository']) / 'deploy' / 'cpu-trial' / 'bootstrap.py'
        shutil.copyfile(bootstrap, folder / 'bootstrap.py')
        remote('install -d -m 700 /opt/spk-cpu-trial/transfer', timeout=30)
        # Explicit allowlist only. Never upload the export directory or ssh/.
        say('3/5 Uploading code, documents, database and the two active models. Please wait...')
        run(['scp', '-B', *options, *ARTIFACTS, 'manifest.json', 'bootstrap.py',
             target + ':/opt/spk-cpu-trial/transfer/'], cwd=folder, timeout=900)
        say('4/5 Installing Docker and restoring the private application. Please wait...')
        remote('python3 -u /opt/spk-cpu-trial/transfer/bootstrap.py', timeout=900)
        say('5/5 Checking API, database and model readiness...')
        ready = None
        for attempt in range(30):
            try:
                raw = remote("python3 -c " + shlex.quote("import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8000/ready',timeout=30).read().decode())"), capture=True, timeout=45)
                ready = json.loads(raw)
                if ready.get('db') and ready.get('llm'):
                    break
            except (subprocess.SubprocessError, ValueError):
                pass
            time.sleep(5)
        else:
            raise RuntimeError('API did not become ready; inspect server logs')
        result.update(status='ready', readiness=ready)
        say('READY: API, database and LLM are available privately. Public access is not enabled.')
        say('Reconnect your VPN and send transfer-result.json or a screenshot to Codex.')
    except Exception as exc:
        result.update(status='failed', error=str(exc))
        say('STOPPED: ' + str(exc))
        say('Reconnect your VPN. Send transfer-result.json and transfer-command.log to Codex.')
        raise
    finally:
        result['finished_utc'] = datetime.now(timezone.utc).isoformat()
        result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        log.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', type=Path, required=True)
    args = parser.parse_args()
    try:
        main(args.config)
    except Exception:
        sys.exit(1)
