"""Worker configuration — loaded from environment or .env file."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class WorkerSettings(BaseSettings):
    # BSNexus server URL
    server_url: str = "http://localhost:8000"

    # Worker identity (from registration)
    worker_token: str = ""
    worker_name: str = ""

    # Polling
    poll_interval_seconds: int = 5
    poll_timeout_ms: int = 5000

    # Claude Code
    workspace_dir: str = "."
    claude_timeout_seconds: int = 3600
    skip_permissions: bool = True

    model_config = SettingsConfigDict(env_file=".env", env_prefix="BSNEXUS_")


settings = WorkerSettings()
