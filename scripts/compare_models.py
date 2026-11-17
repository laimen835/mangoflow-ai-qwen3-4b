"""Small factual comparison on identical retrieved contexts, not a broad benchmark."""
import asyncio
import json
import sys
import time
from pathlib import Path

from src.core.config import settings
from src.core.db.session import AsyncSessionLocal, engine
from src.core.llm.ollama_client import OllamaClient
from src.core.rag.embeddings import build_embedder
from src.core.rag.prompt import build_messages
from src.core.rag.retriever import HybridRetriever

CASES = [
    ("Когда был основан колледж СПК?", ["1964"]),
    ("Предоставляют ли иногородним студентам СПК общежитие?", ["общежит"]),
    ("Какая специальность имеет код 09.02.07?", ["информацион", "программирован"]),
    ("Какая электронная почта приёмной комиссии СПК?", ["priem_spk@ncsa.ru"]),
    ("Какой диплом выдаёт колледж СПК?", ["скга"]),
    ("Кто директор среднепрофессионального колледжа?", ["гурина", "ирина"]),
]


async def main():
    contexts = []
    async with AsyncSessionLocal() as db:
        retriever = HybridRetriever(db, build_embedder(settings), settings)
        for question, expected in CASES:
            hits = await retriever.search(question)
            contexts.append((question, expected, build_messages(question, hits)))
    results = []
    for model in ["llama3.2:3b", "qwen3:4b-instruct"]:
        client = OllamaClient(settings.model_copy(update={"llm_model": model, "llm_timeout_s": 240}))
        for question, expected, messages in contexts:
            start = time.monotonic()
            response = await client.generate(messages, max_tokens=300, temperature=0.1)
            passed = all(term.casefold() in response.text.casefold() for term in expected)
            results.append({"model": model, "question": question, "answer": response.text,
                            "keyword_check": passed, "seconds": round(time.monotonic() - start, 2)})
            print(json.dumps(results[-1], ensure_ascii=False), flush=True)
    Path("logs/model-comparison.json").write_text(json.dumps({"cases": len(CASES), "results": results,
        "limitation": "Six questions and simple keyword checks; manual factual review is still required."},
        ensure_ascii=False, indent=2), encoding="utf-8")
    await engine.dispose()


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    asyncio.run(main())
