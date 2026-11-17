"""Create isolated, backed-up quality workspaces; never reset an existing DB."""
import asyncio,os,subprocess,json
from pathlib import Path
import asyncpg
from sqlalchemy.engine import make_url
from src.core.config import settings

ROOT=Path('D:/Codex-RAG-runtime/quality-90-2026-11-10')


async def main():
    ROOT.mkdir(exist_ok=True)
    url=make_url(settings.database_url)
    env={**os.environ,'PGPASSWORD':url.password}
    args=['-h',url.host,'-p',str(url.port),'-U',url.username]
    backup=ROOT/'before-quality90.dump'
    if not backup.exists():
        subprocess.run(['D:/Codex-RAG-runtime/pgsql/bin/pg_dump.exe',*args,'-Fc','-d',url.database,'-f',str(backup)],env=env,check=True,capture_output=True)
    conn=await asyncpg.connect(host=url.host,port=url.port,user=url.username,password=url.password,database='postgres')
    try:
        for name in ['rag_quality90_baseline','rag_quality90_candidate']:
            if not await conn.fetchval('SELECT 1 FROM pg_database WHERE datname=$1',name):
                await conn.execute('CREATE DATABASE '+name)
                subprocess.run(['D:/Codex-RAG-runtime/pgsql/bin/pg_restore.exe',*args,'-d',name,str(backup)],env=env,check=True,capture_output=True)
            print('Workspace ready:',name,flush=True)
    finally:await conn.close()
    (ROOT/'protocol.json').write_text(json.dumps({
        'objective':'At least 90% complete, source-supported answers across SPK topics; refusals excluded from coverage credit.',
        'scope':['admission','specialties','teachers_contacts','schedule','scholarships_support','hostel','college_services'],
        'weights':'approximately equal, confirmed by user',
        'required_metrics':['substantive_answer_coverage','correct_supported_answers','unsupported_claim_rate','source_correctness','retrieval_latency','full_latency'],
        'existing_evals':'Regression/development evidence only; their holdout has already been inspected.',
        'rollout':'Local only; existing model and index kept until independent acceptance; backup required.',
        'status':'Independent evaluation and retrieval improvement in progress; 90% not yet proven.'},ensure_ascii=False,indent=2),encoding='utf-8')


if __name__=='__main__':asyncio.run(main())
