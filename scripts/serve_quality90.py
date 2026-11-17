"""Serve the isolated candidate corpus for HTTP checks, never public hosting."""
import os,subprocess,sys,argparse
from sqlalchemy.engine import make_url
from src.core.config import settings


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--policy',choices=['profile','evidence'],default='profile')
    parser.add_argument('--review',action='store_true')
    parser.add_argument('--answer-mode',choices=['generative','reviewed_quotes'],default='generative');args=parser.parse_args()
    env={**os.environ,'DATABASE_URL':make_url(settings.database_url).set(database='rag_quality90_candidate').render_as_string(hide_password=False),
         'AUTO_SYNC_ENABLED':'false','RETRIEVAL_POLICY':args.policy,'ANSWER_REVIEW_ENABLED':str(args.review).lower(),
         'ANSWER_MODE':args.answer_mode}
    raise SystemExit(subprocess.call([sys.executable,'-m','uvicorn','src.core.main:app','--host','127.0.0.1','--port','8002'],env=env))
