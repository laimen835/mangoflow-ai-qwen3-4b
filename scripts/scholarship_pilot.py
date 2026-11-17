"""Local pilot ingestion only. Production promotion requires a reviewed report."""
import argparse,asyncio,json,hashlib,os,subprocess
from pathlib import Path
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine,AsyncSession
from tokenizers import Tokenizer
from src.core.config import settings
from src.core.rag.scholarship_ingest import ingest_snapshot
from src.core.rag.embeddings import build_embedder
from src.core.rag.structured_documents import EXTRACTION_VERSION,CHUNK_VERSION
from sqlalchemy import select
from src.core.db.models import Document,DocumentStatus

ROOT=Path('D:/Codex-RAG-runtime/scholarship-pilot')


def input_signature(root=ROOT):
    files=[root/'snapshot-v2/manifest.json',root/'reviews.json',root/'nomic-tokenizer/tokenizer.json',Path('tests/eval/scholarship_cases.json'),Path('src/core/rag/prompt.py'),
           *sorted((root/'snapshot-v2').glob('*.extracted.json'))]
    digest=hashlib.sha256((EXTRACTION_VERSION+CHUNK_VERSION+settings.embedding_model+str(settings.embedding_dim)+settings.llm_model).encode())
    for p in files:digest.update(p.name.encode());digest.update(p.read_bytes())
    return digest.hexdigest()


def check_approval(report,variant,signature):
    gates=report.get('gates',{})
    if not (report.get('promotion_approved') is True and report.get('variant')==variant and report.get('input_signature')==signature
            and gates.get('correct_control_answers',0)>=3 and gates.get('improved_control_answers',0)>=1
            and gates.get('unsupported_control_answers',1)==0 and gates.get('p95_search_ms',float('inf'))<=3000
            and gates.get('regressions_passed') is True and gates.get('tests_passed') is True and gates.get('guardrails_passed') is True):
        raise RuntimeError('Pilot approval missing or inputs changed')


def backup_production():
    u=make_url(settings.database_url);target=ROOT/'before-promotion.dump'
    if target.exists():raise RuntimeError('Promotion backup already exists; review previous attempt before repeating')
    subprocess.run(['D:/Codex-RAG-runtime/pgsql/bin/pg_dump.exe','-h',u.host,'-p',str(u.port),'-U',u.username,
                    '-Fc','-d',u.database,'-f',str(target)],env={**os.environ,'PGPASSWORD':u.password},check=True,capture_output=True)
    return target


async def rollback():
    report=json.loads((ROOT/'production-ingest.json').read_text(encoding='utf-8'))
    engine=create_async_engine(settings.database_url)
    try:
        async with engine.begin() as connection:
            async with AsyncSession(bind=connection,join_transaction_mode='create_savepoint') as db:
                for entry in report['documents']:
                    doc=await db.get(Document,entry['document_id'])
                    if not doc or doc.source_url!=entry['url'] or doc.source_type!='ncsa_scholarship' or doc.meta.get('source_sha256')!=entry['sha256']:raise RuntimeError('Rollback target changed')
                    doc.status=DocumentStatus.ARCHIVED
                await db.commit()
        print('Pilot materials archived. Previous documents and conversations preserved.')
    finally:await engine.dispose()


async def ingest(variant,production=False):
    report_path=ROOT/'acceptance.json'
    if production:
        report=json.loads(report_path.read_text(encoding='utf-8'))
        check_approval(report,variant,input_signature())
        database=make_url(settings.database_url).database
    else:database='rag_scholarship_'+variant
    url=make_url(settings.database_url).set(database=database).render_as_string(hide_password=False)
    cfg=settings.model_copy(update={'database_url':url});engine=create_async_engine(url)
    try:
        # Each ingest_text commit releases only a savepoint. The complete batch
        # becomes visible atomically when this enclosing transaction commits.
        async with engine.begin() as connection:
            async with AsyncSession(bind=connection,join_transaction_mode='create_savepoint',expire_on_commit=False) as db:
                if production:
                    if await db.scalar(select(Document.id).where(Document.source_type=='ncsa_scholarship').limit(1)):
                        raise RuntimeError('Production already contains pilot records; refusing to overwrite previous versions')
                    backup_production()
                result=await ingest_snapshot(db,build_embedder(cfg),cfg,snapshot=ROOT/'snapshot-v2',reviews_path=ROOT/'reviews.json',
                    variant=variant,tokenizer=Tokenizer.from_file(str(ROOT/'nomic-tokenizer/tokenizer.json')))
        target=ROOT/('production-ingest.json' if production else variant+'-ingest.json')
        target.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        print('Committed',len(result['documents']),'materials;',sum(x['reviewed'] for x in result['documents']),'reviewed')
    finally:await engine.dispose()


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('variant',choices=['legacy','structured','rollback']);parser.add_argument('--production',action='store_true');args=parser.parse_args()
    asyncio.run(rollback() if args.variant=='rollback' else ingest(args.variant,args.production))
