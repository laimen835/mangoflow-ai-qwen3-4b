from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from src.core.config import settings

logger = logging.getLogger(__name__)
_lock = asyncio.Lock()
STATUS_FILE = Path("logs/update-status.json")
REPORT_FILE = Path("logs/ncsa-sync.json")


def read_json(path, fallback=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return fallback


def save_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


async def run_update(max_pages=48, max_pdfs=8):
    """Both CLI and the running app use a cross-process lock file."""
    from src.core.db.session import AsyncSessionLocal
    from src.core.rag.embeddings import build_embedder
    from src.core.rag.ncsa_source import sync_ncsa
    if _lock.locked():
        return None
    async with _lock:
        lock_path = Path("logs/source-update.lock")
        lock_path.parent.mkdir(exist_ok=True)
        lock_file = lock_path.open("a+b")
        try:
            if lock_file.tell() == 0:
                lock_file.write(b"0")
                lock_file.flush()
            lock_file.seek(0)
            try:
                import msvcrt
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
            except ImportError:
                import fcntl
                fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                return None
            started = datetime.now(timezone.utc).isoformat()
            save_json(STATUS_FILE, {"state": "running", "started_at": started})
            try:
                async with AsyncSessionLocal() as db:
                    if settings.deep_crawl_enabled:
                        from tokenizers import Tokenizer
                        from src.core.rag.deep_crawler import DeepCrawler
                        from src.core.rag.crawl_policy import CrawlConfig
                        config=CrawlConfig(max_pages=max_pages,max_documents=max_pdfs,render=settings.deep_crawl_render)
                        report=await DeepCrawler(config,settings.deep_crawl_folder,db=db,
                            embedder=build_embedder(settings),settings=settings,
                            tokenizer=Tokenizer.from_file(settings.deep_crawl_tokenizer),
                            office_executable=settings.deep_crawl_office_executable).run(
                                refresh=not read_json(Path(settings.deep_crawl_folder)/'report.json',{}).get('remaining',0))
                        report['remaining_page_links']=report['remaining']
                    else:
                        report = await sync_ncsa(db, build_embedder(settings), settings, max_pages=max_pages, max_pdfs=max_pdfs)
                save_json(REPORT_FILE, report)
                save_json(STATUS_FILE, {"state": "partial" if report["errors"] or report.get('remaining_page_links') else "complete",
                    "started_at": started, "finished_at": report["completed_at"],
                    "documents_checked": len(report["documents"]), "errors": len(report["errors"]),
                    "remaining_page_links": report.get('remaining_page_links', 0)})
                return report
            except asyncio.CancelledError:
                save_json(STATUS_FILE, {"state": "interrupted", "started_at": started})
                raise
            except Exception:
                logger.exception("Source update failed")
                save_json(STATUS_FILE, {"state": "failed", "started_at": started,
                    "finished_at": datetime.now(timezone.utc).isoformat()})
                return None
        finally:
            lock_file.close()


async def scheduled_updates():
    # Runs only while the local API is running. It is not an OS-wide background job.
    while True:
        try:
            report = read_json(REPORT_FILE, {})
            last = report.get("completed_at")
            elapsed = (datetime.now(timezone.utc) - datetime.fromisoformat(last)).total_seconds() if last else float("inf")
            if elapsed >= settings.sync_interval_hours * 3600:
                await run_update()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Scheduled source check failed")
        await asyncio.sleep(60)
