"""Worker configuration — loaded from environment or .env file."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class WorkerSettings(BaseSettings):
    # BSNexus URL — defaults to official SaaS
    server_url: str = "https://nexus.bsvibe.dev"

    # Install token (from Settings → Install Token)
    install_token: str = ""

    # Worker identity (from registration)
    worker_token: str = ""
    worker_name: str = ""

    # Bound project (optional — only accept tasks from this project)
    project_id: str = ""

    # Polling
    poll_interval_seconds: int = 5

    # Execution
    claude_timeout_seconds: int = 3600
    skip_permissions: bool = True
    # How many CLI calls to run in parallel. Each task spawns a separate
    # subprocess, so this is bounded by CPU / memory. Default 5 covers
    # a typical agent delegation chain where CEO dispatches 3-4 reports
    # simultaneously.
    max_parallel_tasks: int = 5

    model_config = SettingsConfigDict(env_file=".env", env_prefix="BSNEXUS_")


settings = WorkerSettings()
