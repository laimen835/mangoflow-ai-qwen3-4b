"""Owner-run resource diagnosis. Does not modify application or server settings."""
import argparse
import ipaddress
import json
from pathlib import Path
import subprocess

REMOTE = r'''
import concurrent.futures, json, os, subprocess, time, urllib.request
from pathlib import Path

base = Path('/opt/spk-cpu-trial/app/deploy/cpu-trial')
c = ['docker','compose','--env-file',str(base/'trial.env'),'-f',str(base/'compose.json')]
def compose(*args):
    return subprocess.check_output(c+list(args),text=True,timeout=30).strip()
def memory():
    data = dict(line.split(':',1) for line in Path('/proc/meminfo').read_text().splitlines())
    return {k:int(data[k].split()[0]) for k in ('MemTotal','MemAvailable','SwapTotal','SwapFree')}
def cpu():
    return [int(v) for v in Path('/proc/stat').read_text().splitlines()[0].split()[1:]]
def cg(name):
    try:
        return compose('exec','-T','ollama','cat','/sys/fs/cgroup/'+name)
    except subprocess.SubprocessError:
        return 'unavailable'
def resources():
    result={}
    for service in ('ollama','api','db'):
        cid=compose('ps','-q',service)
        info=json.loads(subprocess.check_output(['docker','inspect',cid],text=True,timeout=15))[0]
        h=info['HostConfig']
        safe=('OLLAMA_KEEP_ALIVE','OLLAMA_MAX_LOADED_MODELS','OLLAMA_NUM_PARALLEL','OLLAMA_NUM_CTX','LLM_MAX_TOKENS','LLM_TIMEOUT_S','ANSWER_MODE','RETRIEVAL_POLICY')
        result[service]={'limits':{k:h.get(k) for k in ('NanoCpus','CpuQuota','CpuPeriod','CpusetCpus','Memory','MemorySwap')},
                         'settings':{v.split('=',1)[0]:v.split('=',1)[1] for v in info['Config']['Env'] if v.split('=',1)[0] in safe}}
    return result
report={'hostname':subprocess.check_output(['hostname','-s'],text=True).strip(),
        'logical_cpus':os.cpu_count(),'memory_before_kb':memory(),'containers':resources(),
        'cpu_max':cg('cpu.max'),'cgroup_before':cg('cpu.stat'),
        'ollama_ps_before':compose('exec','-T','ollama','ollama','ps'),'questions':[]}
info=Path('/proc/cpuinfo').read_text().splitlines()
report['cpu_model']=next((x.split(':',1)[1].strip() for x in info if x.startswith('model name')),'unknown')
report['cpu_flags']=next((x.split(':',1)[1].strip() for x in info if x.startswith('flags')),'unknown')
question='Какие направления подготовки есть в институте цифровых технологий?'
def ask():
    req=urllib.request.Request('http://127.0.0.1:8000/api/v1/chat',data=json.dumps({'message':question}).encode(),headers={'Content-Type':'application/json'})
    with urllib.request.urlopen(req,timeout=270) as response:
        return json.load(response)
for number in range(2):
    start=time.monotonic(); samples=[]; previous=cpu(); peak_available=memory()['MemAvailable']
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        task=pool.submit(ask)
        while not task.done():
            time.sleep(2); current=cpu(); delta=[b-a for a,b in zip(previous,current)]; total=sum(delta) or 1
            samples.append({'busy_percent':round(100*(total-delta[3]-delta[4]-delta[7])/total,2),
                            'steal_percent':round(100*delta[7]/total,2)})
            previous=current; peak_available=min(peak_available,memory()['MemAvailable'])
        try:
            answer=task.result()
            row={'run':number+1,'seconds':round(time.monotonic()-start,3),'answer':answer.get('answer'),
                 'usage':answer.get('usage'),'sources':answer.get('sources')}
        except Exception as exc:
            row={'run':number+1,'seconds':round(time.monotonic()-start,3),'error':str(exc)}
    row.update(cpu_samples=samples,minimum_available_memory_kb=peak_available,
               cgroup_after=cg('cpu.stat'),memory_after_kb=memory())
    report['questions'].append(row)
report.update(ollama_ps_after=compose('exec','-T','ollama','ollama','ps'),memory_after_kb=memory())
print(json.dumps(report,ensure_ascii=False))
'''


def main(config_path):
    config=json.loads(config_path.read_text(encoding='utf-8-sig'))
    folder=Path(config['export_folder'])
    options=['-i',str(folder/'ssh/spk_cpu_trial_rsa'),'-o','BatchMode=yes','-o','ConnectTimeout=15',
             '-o','StrictHostKeyChecking=yes','-o','UserKnownHostsFile='+(folder/'ssh/known_hosts').as_posix(),
             '-o','ServerAliveInterval=15','-o','ServerAliveCountMax=4']
    target='root@'+str(ipaddress.ip_address(config['host']))
    expected='cv'+str(int(config['server_id']))
    actual=subprocess.check_output(['ssh',*options,target,'hostname -s'],text=True,timeout=30).strip()
    if actual!=expected:
        raise RuntimeError('Unexpected server; refusing diagnosis')
    print('SSH OK. Checking CPU limits, memory and Ollama. Running two identical chat requests.',flush=True)
    print('Keep VPN OFF. This can take up to 10 minutes. No application settings will be changed.',flush=True)
    result=subprocess.run(['ssh',*options,target,'python3 -'],input=REMOTE,text=True,encoding='utf-8',
                          capture_output=True,timeout=660)
    if result.returncode:
        (folder/'cpu-diagnostic-error.log').write_text(result.stderr,encoding='utf-8')
        raise RuntimeError('Remote diagnosis failed; details saved in cpu-diagnostic-error.log')
    report=json.loads(result.stdout)
    path=folder/'cpu-diagnostic.json'
    path.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print('DONE. CPU count:',report['logical_cpus'],flush=True)
    for row in report['questions']:
        samples=row['cpu_samples']
        print('Run',row['run'],'seconds:',row['seconds'],'error:',row.get('error','none'),
              'average CPU busy %:',round(sum(s['busy_percent'] for s in samples)/max(1,len(samples)),1),
              'average steal %:',round(sum(s['steal_percent'] for s in samples)/max(1,len(samples)),1),flush=True)
    print('Result saved:',path,flush=True)
    print('Reconnect VPN and tell Codex DONE. No upload or screenshot is necessary.',flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--config',required=True,type=Path)
    args=parser.parse_args()
    try:
        main(args.config)
    except Exception as exc:
        print('STOPPED:',exc,flush=True)
        raise
