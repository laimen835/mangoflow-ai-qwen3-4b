"""Serve the isolated structured clone on loopback for HTTP regression tests."""
import os,subprocess,sys
from sqlalchemy.engine import make_url
from src.core.config import settings


if __name__=='__main__':
    env={**os.environ,'DATABASE_URL':make_url(settings.database_url).set(database='rag_scholarship_structured').render_as_string(hide_password=False),
         'AUTO_SYNC_ENABLED':'false'}
    raise SystemExit(subprocess.call([sys.executable,'-m','uvicorn','src.core.main:app','--host','127.0.0.1','--port','8002'],env=env))
