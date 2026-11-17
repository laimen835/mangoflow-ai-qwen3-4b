"""python -m scripts.sync_ncsa [--max-pages 16] [--max-pdfs 8]."""
import argparse
import asyncio
import json
import sys
from pathlib import Path

from src.core.config import settings
from src.core.db.session import AsyncSessionLocal, engine
from src.core.rag.embeddings import build_embedder
from src.core.rag.ncsa_source import sync_ncsa
from src.core.services.updates import run_update


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-pages", type=int, default=16)
    parser.add_argument("--max-pdfs", type=int, default=8)
    args = parser.parse_args()
    if not (1 <= args.max_pages <= 100 and 0 <= args.max_pdfs <= 30):
        parser.error("Allowed: 1..100 pages and 0..30 PDFs")
    try:
        report = await run_update(max_pages=args.max_pages, max_pdfs=args.max_pdfs)
        if report is None:
            print("Update already running or failed; check logs/update-status.json")
            raise SystemExit(1)
        Path("logs").mkdir(exist_ok=True)
        Path("logs/ncsa-sync.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Completed: {len(report['documents'])} documents, {len(report['errors'])} errors. Report: logs/ncsa-sync.json")
        if not report["documents"]:
            raise SystemExit(1)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    asyncio.run(main())
