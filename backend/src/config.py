from typing import Optional

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Infrastructure
    redis_url: str = "redis://redis:6379"
    database_url: str = "postgresql+asyncpg://bsnexus:bsnexus_dev@postgres:5432/bsnexus"

    # Supabase Auth
    supabase_jwt_secret: str = ""
    supabase_url: str = ""
    supabase_anon_key: str = ""
    supabase_service_role_key: str = ""

    # BSVibe Auth
    bsvibe_auth_url: str = "https://auth.bsvibe.dev"
    frontend_url: str = "http://localhost:3000"

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
    default_llm_model: str = "anthropic/claude-sonnet-4-20250514"
    default_llm_base_url: Optional[str] = None

    @field_validator("default_llm_model", mode="after")
    @classmethod
    def _coerce_empty_llm_model(cls, v: str) -> str:
        return v or "anthropic/claude-sonnet-4-20250514"

    # Executor
    workspace_dir: str = "/workspace"
    executor_type: str = "claude-code"
    execution_timeout_seconds: int = 3600
    total_execution_timeout_seconds: int = 7200
    rate_limit_retry_count: int = 5
    rate_limit_wait_seconds: int = 300
    executor_skip_permissions: bool = False

    # Auto-redesign
    max_auto_redesigns: int = 2

    # Logging
    log_dir: str = "logs"
    log_level: str = "INFO"


settings = Settings()
