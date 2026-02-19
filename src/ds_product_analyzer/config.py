from pathlib import Path

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}

    # Database
    database_url: str = "sqlite+aiosqlite:///./data.db"

    # Reddit
    reddit_client_id: str = ""
    reddit_client_secret: str = ""
    reddit_user_agent: str = "ds_product_analyzer/0.1"

    # Scheduling intervals
    collect_interval_hours: int = 4
    reddit_interval_hours: int = 1
    amazon_interval_hours: int = 6
    tiktok_interval_hours: int = 6

    # Amazon
    amazon_rate_limit_secs: float = 3.0

    # Sentiment
    sentiment_model: str = "distilbert-base-uncased-finetuned-sst-2-english"

    # Anthropic (for LLM product name extraction)
    anthropic_api_key: str = ""
    llm_extraction_model: str = "claude-haiku-4-20250414"
    llm_extraction_batch_size: int = 40

    # App
    log_level: str = "INFO"
    base_dir: Path = Path(__file__).resolve().parent.parent.parent


settings = Settings()
