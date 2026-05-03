from bsvibe_core import BsvibeSettings
from pydantic_settings import SettingsConfigDict


class Settings(BsvibeSettings):
    """BSNexus settings — extends ``bsvibe_core.BsvibeSettings``.

    Phase A Batch 5: adopt the shared base so the four products share
    one ``case_sensitive=False`` + ``extra="ignore"`` contract. BSNexus
    overrides ``env_file`` to load ``.env`` for local dev (parent
    default is ``None`` because loading is a deployment concern).
    """

    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)

    # Infrastructure
    redis_url: str = "redis://redis:6379"
    database_url: str = "postgresql+asyncpg://bsnexus:bsnexus_dev@postgres:5432/bsnexus"

    # Database connection pool (S2-1 M19/H13). Each long-running
    # delegated agent can hold a session for the duration of an LLM
    # call — production deployments need to grow these without
    # patching source. ``db_pool_recycle_s = -1`` means "no recycle";
    # set a positive value when running behind a connection-killing
    # proxy (PgBouncer pause, RDS proxy idle drop) so SQLAlchemy
    # reconnects before the proxy reaps the socket.
    db_pool_size: int = 10
    db_max_overflow: int = 20
    db_pool_timeout_s: int = 60
    db_pool_recycle_s: int = -1

    # BSVibe Auth
    bsvibe_auth_url: str = "https://auth.bsvibe.dev"
    frontend_url: str = "http://localhost:3000"

    # Security - keys
    prompt_signing_key: str = "dev-signing-key-change-in-production"
    encryption_key: str = "dev-encryption-key-change-in-production"
    # Direction reset 2026-05-03 — BSNexus MCP server signing key.
    # Run-scoped HMAC tokens are minted per BSGateway chat completion
    # and verified on every /mcp/sse connect. Compromise impersonates
    # any in-flight run; rotate by restarting all instances.
    mcp_signing_key: str = "dev-mcp-signing-key-change-in-production"
    # Address BSGateway workers reach to talk to this BSNexus instance's
    # MCP server. Default is the dev devcontainer port; production sets
    # the internal service URL.
    mcp_internal_url: str = "http://localhost:18100"

    # Security - CORS
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

    # Per-project workspace root — where run outputs land as real files.
    workspace_root: str = "./data/workspaces"

    # Claude Code executor (worker path)
    workspace_dir: str = "/workspace"
    execution_timeout_seconds: int = 3600
    total_execution_timeout_seconds: int = 7200
    rate_limit_retry_count: int = 5
    rate_limit_wait_seconds: int = 300

    # Logging
    log_dir: str = "logs"
    log_level: str = "INFO"

    # Deployment environment — controls security guards that must NOT
    # be active in production. The string ``"production"`` (case-
    # insensitive) disables the E2E test-token bypass regardless of
    # ``e2e_test_token``'s value. Anything else (``"development"``,
    # ``"staging"``, empty) treats the bypass as acceptable.
    environment: str = ""

    # E2E test bypass — only honored when ``environment`` is non-prod.
    e2e_test_token: str = ""
    e2e_test_user_id: str = "e2e-test-user"
    e2e_test_user_email: str = "e2e@bsnexus.test"
    e2e_test_user_tenant_id: str = "11111111-1111-4111-8111-111111111111"

    # ── Phase 0 P0.5 + P0.7 ─────────────────────────────────────────
    # bsvibe-authz / service-JWT integration. ``service_token_signing_secret``
    # is shared with BSVibe-Auth's ``/api/service-tokens/issue`` endpoint
    # for HS256 signing in Phase 0 (per ``bsvibe-authz`` package
    # docs). Empty default so dev environments without OpenFGA can boot;
    # production must set a real value.
    service_token_signing_secret: str = ""

    # Long-lived service-account credential (Bearer access token issued
    # by BSVibe-Auth) used by ``ServiceJWTMinter`` to authenticate
    # itself to ``/api/service-tokens/issue``. The service-account user
    # MUST have admin/owner role for the tenants whose tokens this
    # process mints. Empty in dev → minter falls back to anonymous and
    # downstream services 401 (Noop fallback in adapters).
    bsnexus_service_account_token: str = ""


settings = Settings()
