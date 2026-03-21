from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Infrastructure
    redis_url: str = "redis://redis:6379"
    database_url: str = "postgresql+asyncpg://bsnexus:bsnexus_dev@postgres:5432/bsnexus"

    # Security - keys
    prompt_signing_key: str = "dev-signing-key-change-in-production"
    encryption_key: str = "dev-encryption-key-change-in-production"

    # Security - CORS
    cors_allowed_origins: list[str] = ["*"]

    # Security - rate limiting
    rate_limit_enabled: bool = True

    # Security - HSTS
    enable_hsts: bool = False
    hsts_max_age: int = 31536000

    # Server
    server_host: str = "0.0.0.0"
    server_port: int = 8000
    debug: bool = False

    # LLM Defaults (fallback only - used when not specified at runtime)
    default_llm_model: Optional[str] = None
    default_llm_base_url: Optional[str] = None

    # Executor
    executor_type: str = "claude-code"
    execution_timeout_seconds: int = 3600
    rate_limit_retry_count: int = 5
    rate_limit_wait_seconds: int = 300
    executor_skip_permissions: bool = True

    # Auto-redesign
    max_auto_redesigns: int = 2

    # Logging
    log_dir: str = "logs"
    log_level: str = "INFO"


settings = Settings()
