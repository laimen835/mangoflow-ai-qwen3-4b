# src/core/config.py
from functools import lru_cache
from datetime import date
from typing import Literal
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", ".env.local"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- DB ---
    database_url: str = Field(default="postgresql+asyncpg://postgres@localhost:5432/rag",repr=False)
    sql_echo: bool = False

    # --- Storage ---
    raw_dir: str = "data/raw"
    storage_dir: str = "data/storage"

    # --- Embeddings ---
    embedding_provider: str = "openai"
    embedding_model: str = "text-embedding-3-small"
    embedding_dim: int = 1536

    # --- Chunking / Retrieval ---
    chunk_size: int = 800
    chunk_overlap: int = 120
    top_k_vector: int = 20
    top_k_bm25: int = 20
    top_k_final: int = 6
    rrf_k: int = 60
    fts_language: str = "russian"
    retrieval_policy: Literal['profile','evidence','multi_query'] = 'profile'
    multi_query_rerank: bool = False
    deep_crawl_enabled: bool = False
    deep_crawl_folder: str = "data/raw/deep-crawl"
    deep_crawl_tokenizer: str = "D:/Codex-RAG-runtime/scholarship-pilot/nomic-tokenizer/tokenizer.json"
    deep_crawl_render: bool = False
    deep_crawl_office_executable: str = ""
    answer_review_enabled: bool = False
    answer_mode: Literal['generative', 'reviewed_quotes', 'source_quotes'] = 'generative'
    reranker_directory: str = "D:/Codex-RAG-runtime/models/reranker-mmarco-avx2"
    reranker_candidates: int = Field(default=20,ge=1,le=20)
    academy_rerank_enabled: bool = False
    academy_department_navigation: bool = False
    academy_reranker_directory: str = ""

    # --- LLM ---
    llm_provider: str = "openai"
    llm_model: str = "gpt-4o-mini"
    llm_timeout_s: float = 30.0
    llm_temperature: float = 0.2
    llm_max_tokens: int = 800
    openai_api_key: str = Field(default="",repr=False)
    openai_base_url: str = ""
    ollama_base_url: str = "http://localhost:11434"
    ollama_think: bool | None = None
    ollama_num_ctx: int = Field(default=4096, ge=2048, le=32768)

    # --- HTTP ---
    cors_origins: list[str] = ["http://localhost:5173"]
    admin_api_key: str = Field(default="",repr=False)
    sync_interval_hours: int = Field(default=6, ge=1, le=168)
    auto_sync_enabled: bool = True
    tesseract_cmd: str = "D:/Codex-RAG-runtime/tesseract/tesseract.exe"
    # Set only after the college confirms an actual numerator/denominator anchor.
    academic_week_anchor: date | None = None
    academic_week_anchor_type: Literal["numerator", "denominator"] = "numerator"
    academic_week_source: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
