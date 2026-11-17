"""Publish the existing guest gateway over HTTPS; never expose the full API."""
import argparse
import ipaddress
import json
from pathlib import Path
import re
import shlex
import subprocess

REMOTE = r'''
import http.cookiejar, json, socket, subprocess, sys, time, urllib.error, urllib.request
from pathlib import Path
from uuid import uuid4

domain=sys.argv[1]
base=Path('/opt/spk-cpu-trial/app/deploy/cpu-trial')
path=base/'compose.json'
before=path.read_bytes()
data=json.loads(before)
if socket.gethostbyname(domain)!=sys.argv[2]:
    raise RuntimeError('Public DNS does not point to this server')
c=['docker','compose','--env-file',str(base/'trial.env'),'-f',str(path)]
def run(*args):
    subprocess.run(c+list(args),check=True,timeout=300)
def get(url,opener=None,body=None):
    req=urllib.request.Request(url,data=json.dumps(body).encode() if body is not None else None,
                               headers={'Content-Type':'application/json'} if body is not None else {})
    with (opener.open(req,timeout=240) if opener else urllib.request.urlopen(req,timeout=30)) as r:
        return r.status,r.read(),r.headers
def status(url):
    try:
        return get(url)[0]
    except urllib.error.HTTPError as exc:
        return exc.code

# Preserve original configuration and keep local-only backends on loopback.
backup=base/'compose.before-public.json'
if not backup.exists():
    backup.write_bytes(before)
ports=data['services']['api'].setdefault('ports',[])
if any(p!='127.0.0.1:8000:8000' and p!='127.0.0.1:8001:8001' for p in ports):
    raise RuntimeError('Unexpected existing API publication; review manually')
if '127.0.0.1:8001:8001' not in ports:
    ports.append('127.0.0.1:8001:8001')
data['services']['guest']={
    'image':'spk-cpu-trial-api:latest','pull_policy':'never',
    'network_mode':'service:api','depends_on':['api'],'restart':'unless-stopped',
    'command':['python','-m','uvicorn','src.core.share:app','--host','0.0.0.0','--port','8001','--no-access-log']}
data['services']['https']={
    'image':'caddy:2','network_mode':'host','depends_on':['guest'],'restart':'unless-stopped',
    'volumes':['./Caddyfile:/etc/caddy/Caddyfile:ro','./state/caddy/data:/data','./state/caddy/config:/config']}
(base/'Caddyfile').write_text(domain+' {\n    reverse_proxy 127.0.0.1:8001 {\n        flush_interval -1\n    }\n}\n')
path.write_text(json.dumps(data,indent=2))
try:
    run('up','-d','--no-build','api','guest')
    for attempt in range(45):
        try:
            ready=json.loads(get('http://127.0.0.1:8001/ready')[1])
            if ready.get('db') and ready.get('llm'):
                break
        except Exception:
            pass
        time.sleep(2)
    else:
        raise RuntimeError('Guest gateway not ready')
    # Check isolation before exposing HTTPS.
    for route in ('/docs','/openapi.json','/.env','/api/v1/admin/search/debug','/api/tags'):
        if status('http://127.0.0.1:8001'+route) not in (404,405):
            raise RuntimeError('Guest route is not blocked: '+route)
    for route in ('overview','teachers','schedule','sources','source-status'):
        if get('http://127.0.0.1:8001/api/v1/portal/'+route)[0]!=200:
            raise RuntimeError('Portal route unavailable: '+route)
    run('up','-d','--no-build','https')
    origin='https://'+domain
    tls_error=None
    for attempt in range(90):
        try:
            ready=json.loads(get(origin+'/ready')[1])
            if ready.get('db') and ready.get('llm'):
                break
        except Exception as exc:
            tls_error=str(exc)
        time.sleep(3)
    else:
        raise RuntimeError('HTTPS certificate/readiness not ready: '+str(tls_error))
    cookies=http.cookiejar.CookieJar()
    client=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cookies))
    public_id=str(uuid4())
    _,raw,headers=get(origin+'/api/v1/chat',client,{'message':'Кто директор института цифровых технологий?','session_id':public_id})
    answer=json.loads(raw)
    if not answer.get('answer') or answer.get('session_id')!=public_id:
        raise RuntimeError('Guest chat/session check failed')
    history=origin+'/api/v1/portal/conversations/'+public_id
    if not json.loads(get(history,client)[1]).get('items'):
        raise RuntimeError('Guest cannot restore its own history')
    outsider=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    if json.loads(get(history,outsider)[1]).get('items'):
        raise RuntimeError('Guest history isolation failed')
    if not any(cookie.name=='spk_guest' and cookie.secure for cookie in cookies):
        raise RuntimeError('Secure guest cookie missing')
    _,stream,_=get(origin+'/api/v1/chat/stream',client,{'message':'Кто директор института цифровых технологий?','session_id':public_id})
    events=[json.loads(line) for line in stream.splitlines() if line.strip()]
    if not events or events[-1].get('type')!='done' or events[-1]['data'].get('session_id')!=public_id:
        raise RuntimeError('Public streaming check failed')
    result={'status':'published','url':origin,'ready':ready,'portal_routes':'passed',
            'history_isolation':'passed','stream':'passed','https_certificate':'verified',
            'answer':answer['answer'],'public_access':True}
    (base/'public-result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
    print('PUBLIC_RESULT='+json.dumps(result,ensure_ascii=False),flush=True)
except Exception:
    # On an incomplete setup, close the public listener. Keep private app/data.
    try:
        subprocess.run(c+['stop','https'],timeout=30,check=False)
    except Exception:
        pass
    raise
'''


def main(config_path):
    config=json.loads(config_path.read_text(encoding='utf-8-sig'))
    folder=Path(config['export_folder'])
    host=str(ipaddress.ip_address(config['host']))
    domain='spk.'+host.replace('.','-')+'.sslip.io'
    if not re.fullmatch(r'[a-z0-9.-]+',domain):
        raise ValueError('Invalid hostname')
    options=['-i',str(folder/'ssh/spk_cpu_trial_rsa'),'-o','BatchMode=yes','-o','ConnectTimeout=15',
             '-o','StrictHostKeyChecking=yes','-o','UserKnownHostsFile='+(folder/'ssh/known_hosts').as_posix(),
             '-o','ServerAliveInterval=15','-o','ServerAliveCountMax=4']
    target='root@'+host
    actual=subprocess.check_output(['ssh',*options,target,'hostname -s'],text=True,timeout=30).strip()
    if actual!='cv'+str(int(config['server_id'])):
        raise RuntimeError('Unexpected server; publication cancelled')
    print('Publishing the guest gateway with HTTPS:',domain,flush=True)
    print('Keep VPN OFF. Allow up to 10 minutes for certificate issuance and checks.',flush=True)
    result=subprocess.run(['ssh',*options,target,'python3 - '+shlex.quote(domain)+' '+shlex.quote(host)],
                          input=REMOTE,text=True,encoding='utf-8',capture_output=True,timeout=1000)
    (folder/'publish-command.log').write_text(result.stdout+'\n'+result.stderr,encoding='utf-8')
    if result.returncode:
        raise RuntimeError('Publication was not confirmed. See publish-command.log before retrying.')
    line=next((x for x in result.stdout.splitlines() if x.startswith('PUBLIC_RESULT=')),None)
    if not line:
        raise RuntimeError('No verified publication result; inspect log')
    report=json.loads(line.removeprefix('PUBLIC_RESULT='))
    (folder/'public-result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print('DONE. Public URL:',report['url'],flush=True)
    print('Reconnect VPN and tell Codex DONE. The site now runs independently of this PC.',flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--config',required=True,type=Path)
    args=parser.parse_args()
    try:
        main(args.config)
    except Exception as exc:
        print('STOPPED:',exc,flush=True)
        raise
