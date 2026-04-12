from typing import Literal, Optional

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Infrastructure
    redis_url: str = "redis://redis:6379"
    database_url: str = "postgresql+asyncpg://bsnexus:bsnexus_dev@postgres:5432/bsnexus"

    # BSVibe Auth
    bsvibe_auth_url: str = "https://auth.bsvibe.dev"
    frontend_url: str = "http://localhost:3000"

    # Security - keys
    prompt_signing_key: str = "dev-signing-key-change-in-production"
    encryption_key: str = "dev-encryption-key-change-in-production"

    # Security - CORS
    # SECURITY: empty by default — must be explicitly configured per environment
    cors_allowed_origins: list[str] = []

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

    # Claude Code executor (worker path)
    workspace_dir: str = "/workspace"
    execution_timeout_seconds: int = 3600
    total_execution_timeout_seconds: int = 7200
    rate_limit_retry_count: int = 5
    rate_limit_wait_seconds: int = 300
    executor_skip_permissions: bool = False

    # Providers
    gateway_provider: Literal["bsgateway", "litellm"] = "litellm"
    knowledge_provider: Literal["bsage", "local"] = "local"

    # Providers — BSGateway
    bsgateway_url: str = ""
    bsgateway_api_key: str = ""

    # Providers — BSage (knowledge)
    bsage_url: str = ""
    bsage_api_key: str = ""

    # Providers — Local knowledge
    knowledge_dir: str = "./knowledge"

    # Logging
    log_dir: str = "logs"
    log_level: str = "INFO"

    # E2E test bypass
    e2e_test_token: str = ""
    e2e_test_user_id: str = "e2e-test-user"
    e2e_test_user_email: str = "e2e@bsnexus.test"
    e2e_test_user_tenant_id: str = "11111111-1111-4111-8111-111111111111"


settings = Settings()
