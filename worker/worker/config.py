"""Worker configuration — loaded from environment or .env file."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class WorkerSettings(BaseSettings):
    # BSNexus URL — defaults to official SaaS
    server_url: str = "https://nexus.bsvibe.dev"

    # Worker identity (from registration)
    worker_token: str = ""
    worker_name: str = ""

    # Bound project (optional — only accept tasks from this project)
    project_id: str = ""

    # Polling
    poll_interval_seconds: int = 5

    # Claude Code execution
    claude_timeout_seconds: int = 3600
    skip_permissions: bool = True

    model_config = SettingsConfigDict(env_file=".env", env_prefix="BSNEXUS_")


settings = WorkerSettings()
