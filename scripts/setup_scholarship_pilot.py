import asyncio,os,subprocess,json
from pathlib import Path
from urllib.parse import urlsplit,unquote
import asyncpg
import httpx,shutil
from src.core.config import settings
from src.core.rag.scholarship_source import collect_grants

async def main():
 root=Path('D:/Codex-RAG-runtime/scholarship-pilot');root.mkdir(exist_ok=True)
 p=urlsplit(settings.database_url);env={**os.environ,'PGPASSWORD':unquote(p.password)}
 args=['-h',p.hostname,'-p',str(p.port),'-U',unquote(p.username)]
 backup=root/'baseline.dump'
 if not backup.exists():subprocess.run(['D:/Codex-RAG-runtime/pgsql/bin/pg_dump.exe',*args,'-Fc','-d',p.path[1:],'-f',str(backup)],env=env,check=True,capture_output=True)
 conn=await asyncpg.connect(host=p.hostname,port=p.port,user=unquote(p.username),password=unquote(p.password),database='postgres')
 for name in ['rag_scholarship_baseline','rag_scholarship_legacy','rag_scholarship_structured']:
  if not await conn.fetchval('SELECT 1 FROM pg_database WHERE datname=$1',name):
   await conn.execute('CREATE DATABASE '+name)
   subprocess.run(['D:/Codex-RAG-runtime/pgsql/bin/pg_restore.exe',*args,'-d',name,str(backup)],env=env,check=True,capture_output=True)
  print('Local clone ready:',name,flush=True)
 await conn.close()
 raw=root/'snapshot-v2'
 if not (raw/'manifest.json').exists():await collect_grants(raw)
 m=json.loads((raw/'manifest.json').read_text(encoding='utf-8'))
 print('Snapshot',len(m['items']),'items; errors',len(m['errors']),flush=True)
 token_dir=root/'nomic-tokenizer';token_dir.mkdir(exist_ok=True)
 revision='e9b6763023c676ca8431644204f50c2b100d9aab'
 if not (token_dir/'tokenizer.json').exists():
  with httpx.Client(timeout=90,follow_redirects=True) as client:
   response=client.get(f'https://huggingface.co/nomic-ai/nomic-embed-text-v1.5/resolve/{revision}/tokenizer.json');response.raise_for_status()
  (token_dir/'tokenizer.json').write_bytes(response.content);(token_dir/'revision.txt').write_text(revision)
 if not (root/'reviews.json').exists():shutil.copyfile('tests/eval/scholarship_review.json',root/'reviews.json')
 import pymupdf
 for item in m['items']:
  path=raw/item['file']
  if path.suffix=='.pdf':
   with pymupdf.open(path) as pdf:pages=[p.get_text(sort=True) for p in pdf]
   (raw/(item['file']+'.txt')).write_text('\n'.join(f'PAGE {i+1}\n{t}' for i,t in enumerate(pages)),encoding='utf-8')
   print(item['file'],len(pages),'pages',sum(map(len,pages)),'characters',item['title'][:70],flush=True)

if __name__=='__main__':asyncio.run(main())
