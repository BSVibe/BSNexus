"""BSNexus CLI — AI가 직접 사용 가능한 CLI 인터페이스."""

import asyncio
import sys
from functools import wraps
from typing import Any

import click


def async_command(f: Any) -> Any:
    """Decorator to run async click commands."""

    @wraps(f)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        return asyncio.run(f(*args, **kwargs))

    return wrapper


@click.group()
def cli() -> None:
    """BSNexus — AI-powered development management system."""


@cli.group()
def architect() -> None:
    """Architect design session management."""


@cli.group()
def pm() -> None:
    """PM orchestration management."""


# ── Architect Commands ─────────────────────────────────────────────


@architect.command("list")
@async_command
async def architect_list() -> None:
    """List all design sessions."""
    from backend.src.cli._helpers import get_db_session

    async with get_db_session() as db:
        from backend.src.core.architect_service import ArchitectService

        service = ArchitectService(db)
        sessions = await service.list_sessions()

    for s in sessions:
        status = s.status.value
        name = s.name or "(unnamed)"
        msg_count = len(s.messages)
        click.echo(f"  {s.id}  [{status}]  {name}  ({msg_count} messages)")

    if not sessions:
        click.echo("  No sessions found.")


@architect.command("create")
@click.option("--name", default=None, help="Session name")
@click.option("--api-key", required=True, envvar="LLM_API_KEY", help="LLM API key")
@click.option("--model", default="anthropic/claude-sonnet-4-20250514", help="LLM model")
@click.option("--base-url", default=None, help="LLM base URL")
@async_command
async def architect_create(name: str | None, api_key: str, model: str, base_url: str | None) -> None:
    """Create a new design session."""
    from backend.src.cli._helpers import get_db_session

    llm_config: dict[str, Any] = {"api_key": api_key, "model": model}
    if base_url:
        llm_config["base_url"] = base_url

    async with get_db_session() as db:
        from backend.src.core.architect_service import ArchitectService

        service = ArchitectService(db)
        session = await service.create_session(llm_config, name=name)

    click.echo(f"Session created: {session.id}")


@architect.command("chat")
@click.argument("session_id")
@click.argument("message")
@async_command
async def architect_chat(session_id: str, message: str) -> None:
    """Send a message to a design session."""
    import uuid as _uuid
    from backend.src.cli._helpers import get_db_session
    from backend.src.core.llm_client import LLMError

    try:
        sid = _uuid.UUID(session_id)
    except ValueError:
        click.echo(f"Error: Invalid session ID: {session_id}", err=True)
        sys.exit(1)

    async with get_db_session() as db:
        from backend.src.core.architect_service import ArchitectService

        service = ArchitectService(db)
        try:
            _, cleaned_text, has_finalize, _ = await service.send_message(sid, message)
        except ValueError as e:
            click.echo(f"Error: {e}", err=True)
            sys.exit(1)
        except LLMError as e:
            click.echo(f"LLM Error: {e}", err=True)
            sys.exit(1)

    click.echo(cleaned_text)
    if has_finalize:
        click.echo("\n--- Design is ready for finalization. Run: bsnexus architect finalize <session_id> ---")


@architect.command("finalize")
@click.argument("session_id")
@click.option("--repo-path", required=True, help="Path to the target repository")
@async_command
async def architect_finalize(session_id: str, repo_path: str) -> None:
    """Finalize a design session into a project."""
    import uuid as _uuid
    from backend.src.cli._helpers import get_db_session
    from backend.src.core.llm_client import LLMError

    try:
        sid = _uuid.UUID(session_id)
    except ValueError:
        click.echo(f"Error: Invalid session ID: {session_id}", err=True)
        sys.exit(1)

    async with get_db_session() as db:
        from backend.src.core.architect_service import ArchitectService
        from backend.src.storage.database import async_session

        service = ArchitectService(db, session_factory=async_session)
        try:
            project = await service.finalize(sid, repo_path=repo_path)
        except ValueError as e:
            click.echo(f"Error: {e}", err=True)
            sys.exit(1)
        except LLMError as e:
            click.echo(f"LLM Error: {e}", err=True)
            sys.exit(1)

    click.echo(f"Project created: {project.id}")
    click.echo(f"  Name: {project.name}")
    phase_count = len(project.phases) if project.phases else 0
    click.echo(f"  Phases: {phase_count}")


@architect.command("get")
@click.argument("session_id")
@async_command
async def architect_get(session_id: str) -> None:
    """Get session details."""
    import uuid as _uuid
    from backend.src.cli._helpers import get_db_session

    try:
        sid = _uuid.UUID(session_id)
    except ValueError:
        click.echo(f"Error: Invalid session ID: {session_id}", err=True)
        sys.exit(1)

    async with get_db_session() as db:
        from backend.src.core.architect_service import ArchitectService

        service = ArchitectService(db)
        session = await service.get_session(sid)

    if session is None:
        click.echo("Session not found.", err=True)
        sys.exit(1)

    click.echo(f"Session: {session.id}")
    click.echo(f"  Status: {session.status.value}")
    click.echo(f"  Name: {session.name or '(unnamed)'}")
    if session.project_id:
        click.echo(f"  Project: {session.project_id}")
    click.echo(f"  Messages: {len(session.messages)}")
    for m in session.messages:
        role = m.role.value
        preview = m.content[:100].replace("\n", " ")
        click.echo(f"    [{role}] {preview}{'...' if len(m.content) > 100 else ''}")


# ── PM Commands ────────────────────────────────────────────────────


@pm.command("start")
@click.argument("project_id")
@async_command
async def pm_start(project_id: str) -> None:
    """Start orchestration for a project."""
    import uuid as _uuid
    from backend.src.cli._helpers import build_orchestrator

    try:
        pid = _uuid.UUID(project_id)
    except ValueError:
        click.echo(f"Error: Invalid project ID: {project_id}", err=True)
        sys.exit(1)

    click.echo(f"Starting orchestration for project {pid}...")
    orchestrator, db_factory = await build_orchestrator()
    try:
        await orchestrator.start(pid, db_factory)
    except KeyboardInterrupt:
        await orchestrator.stop()
        click.echo("Orchestration stopped.")


@pm.command("status")
@click.argument("project_id")
@async_command
async def pm_status(project_id: str) -> None:
    """Get project orchestration status."""
    import uuid as _uuid
    from backend.src.cli._helpers import get_db_session

    try:
        pid = _uuid.UUID(project_id)
    except ValueError:
        click.echo(f"Error: Invalid project ID: {project_id}", err=True)
        sys.exit(1)

    async with get_db_session() as db:
        from backend.src.repositories.task_repository import TaskRepository
        from backend.src.repositories.phase_repository import PhaseRepository

        repo = TaskRepository(db)
        phase_repo = PhaseRepository(db)

        active_phase = await phase_repo.get_active_phase(pid)
        tasks = await repo.list_by_project(pid)

    status_counts: dict[str, int] = {}
    for t in tasks:
        status_counts[t.status.value] = status_counts.get(t.status.value, 0) + 1

    click.echo(f"Project: {pid}")
    if active_phase:
        click.echo(f"  Active Phase: {active_phase.name}")
    click.echo(f"  Tasks: {len(tasks)} total")
    for status, count in sorted(status_counts.items()):
        click.echo(f"    {status}: {count}")


def main() -> None:
    """CLI entry point."""
    cli()


if __name__ == "__main__":
    main()
