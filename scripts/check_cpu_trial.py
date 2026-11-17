"""Owner-run private cloud verification and preview, while VPN is off."""
import argparse
import asyncio
import ipaddress
import json
from pathlib import Path
import socket
import subprocess
import time
import urllib.request
import webbrowser

REMOTE_CHECK = r'''
import json, subprocess
from pathlib import Path
base = Path('/opt/spk-cpu-trial')
dep = base / 'app/deploy/cpu-trial'
c = ['docker','compose','--env-file',str(dep/'trial.env'),'-f',str(dep/'compose.json')]
def command(*args):
    return subprocess.check_output(c + list(args), text=True).strip()
tags = json.loads(command('exec','-T','api','python','-c',"import urllib.request; print(urllib.request.urlopen('http://ollama:11434/api/tags',timeout=20).read().decode())"))
actual = {m['name']:m['digest'] for m in tags['models']}
expected = json.loads((base/'transfer/manifest.json').read_text())['models']
models_match = all(actual.get(m['name']) == m['digest'].removeprefix('sha256:') for m in expected)
counts = command('exec','-T','db','psql','-U','postgres','-d','rag','-Atc',"SELECT json_build_object('documents',(SELECT count(*) FROM documents),'chunks',(SELECT count(*) FROM chunks))")
port = command('port','api','8000')
print(json.dumps({'models_match':models_match,'models':actual,'database':json.loads(counts),'api_binding':port}))
'''


def main(config_path):
    config = json.loads(config_path.read_text(encoding='utf-8-sig'))
    folder = Path(config['export_folder'])
    target = 'root@' + str(ipaddress.ip_address(config['host']))
    options = ['-i',str(folder/'ssh/spk_cpu_trial_rsa'),'-o','BatchMode=yes',
               '-o','ConnectTimeout=15','-o','StrictHostKeyChecking=yes',
               '-o','UserKnownHostsFile='+(folder/'ssh/known_hosts').as_posix(),
               '-o','ServerAliveInterval=15','-o','ServerAliveCountMax=4']
    print('1/3 Checking the exact model digests, imported documents and private API binding...',flush=True)
    raw = subprocess.check_output(['ssh',*options,target,'python3 -'],input=REMOTE_CHECK,text=True,encoding='utf-8',timeout=90)
    verification = json.loads(raw)
    (folder/'cloud-verification.json').write_text(json.dumps(verification,ensure_ascii=False,indent=2),encoding='utf-8')
    if not (verification['models_match'] and verification['database']['documents'] > 0 and verification['database']['chunks'] > 0 and verification['api_binding'] == '127.0.0.1:8000'):
        raise RuntimeError('Verification failed; review cloud-verification.json')
    print('Verified: model digests match, documents/chunks exist, API is bound to loopback.',flush=True)
    # Do not replace another running local service or tunnel.
    with socket.socket() as probe:
        probe.bind(('127.0.0.1',8003))
    with (folder/'cloud-tunnel.log').open('ab') as log:
        tunnel = subprocess.Popen(['ssh',*options,'-o','ExitOnForwardFailure=yes','-N','-L','127.0.0.1:8003:127.0.0.1:8000',target],stdout=log,stderr=log)
        try:
            base = 'http://127.0.0.1:8003'
            for attempt in range(20):
                if tunnel.poll() is not None:
                    raise RuntimeError('Tunnel exited; inspect cloud-tunnel.log')
                try:
                    with urllib.request.urlopen(base+'/ready',timeout=2) as response:
                        if response.status == 200:
                            break
                except OSError:
                    time.sleep(1)
            else:
                raise RuntimeError('Private tunnel did not become ready')
            print('2/3 Measuring six chat answers (up to 15 minutes). Keep VPN off; please wait...',flush=True)
            from benchmark_cpu_trial import benchmark
            asyncio.run(asyncio.wait_for(benchmark(base,folder/'benchmark-cloud.json'),timeout=900))
            print('3/3 TESTS FINISHED. Opening the cloud application for your own private preview.',flush=True)
            print('This page uses the cloud VM through SSH, not the original local API.',flush=True)
            webbrowser.open(base+'/')
            input('Try the app with VPN OFF. When finished, press Enter here to close the private connection. ')
        finally:
            tunnel.terminate()
            try:
                tunnel.wait(timeout=10)
            except subprocess.TimeoutExpired:
                tunnel.kill()
    print('DONE. Turn VPN back on and tell Codex. Results are saved on drive D.',flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--config',required=True,type=Path)
    args = parser.parse_args()
    try:
        main(args.config)
    except Exception as exc:
        print('CHECK STOPPED: '+str(exc),flush=True)
        raise
