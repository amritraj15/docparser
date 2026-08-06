from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Storage
    database_url: str = "sqlite:///./docparser.db"
    upload_dir: str = "./uploads"

    # LLM
    llm_provider: str = "anthropic"  # "anthropic" | "ollama" | "openrouter"
    anthropic_api_key: str = ""
    extraction_model: str = "claude-sonnet-4-6"

    # Local (Ollama) extraction — used when llm_provider == "ollama"
    ollama_host: str = "http://localhost:11434"
    ollama_model: str = "llama3.2-vision"
    ollama_max_pages: int = 5  # cap pages rendered to images per document, to bound context/latency
    ollama_timeout_seconds: float = 120.0

    # OpenRouter — used when llm_provider == "openrouter". A cloud aggregator (like
    # Anthropic direct), not a privacy-preserving option - do not use for the repo-
    # suggestion embedding path (that stays hard-locked to local Ollama regardless).
    openrouter_api_key: str = ""
    openrouter_model: str = "tencent/hy3:free"  # free, and explicitly documented by
                            # OpenRouter as having stable tool-calling - checked directly
                            # against openrouter.ai/collections/tool-calling-models, where
                            # it ranks #1 by usage among free tool-calling models. Not yet
                            # tested with a real call against THIS pipeline's exact PDF +
                            # forced-tool-choice shape. See decisions.md #20 addendum 4.
                            # Fall back to "openrouter/free" (auto-router) or a paid model
                            # (e.g. "anthropic/claude-sonnet-4.6") if this stops working.
    openrouter_site_url: str = ""   # optional, sent as HTTP-Referer for openrouter.ai rankings
    openrouter_app_name: str = "docparser"  # optional, sent as X-Title
    openrouter_timeout_seconds: float = 120.0

    # Pipeline behavior
    review_confidence_threshold: float = 0.75  # fields below this go to the review queue
    max_upload_bytes: int = 20 * 1024 * 1024  # 20MB, matches Claude's PDF size ceiling

    # Repo change-suggestion (RAG over a local codebase) — OFF by default on purpose.
    # This points at a LOCAL FOLDER PATH, never a URL and never uploaded anywhere. Embedding
    # is hard-locked to a local Ollama model in code (see repo_index.py) regardless of
    # LLM_PROVIDER, because sending proprietary code to a cloud API to embed it is a real
    # confidentiality leak even when cloud Claude is fine for reading a public BSE circular.
    repo_suggestion_enabled: bool = False
    backend_repo_path: str = ""
    frontend_repo_path: str = ""
    repo_index_dir: str = "./repo_index"  # local cache of embeddings — gitignored, never committed
    repo_embedding_model: str = "nomic-embed-text"  # local Ollama embedding model
    repo_similarity_threshold: float = 0.35  # below this, we report "no match" rather than force a guess

    class Config:
        env_file = ".env"


settings = Settings()
