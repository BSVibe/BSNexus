"""Tests for the import-project flow.

These exercise the source/storage providers directly (the ImportSource
and WorkspaceStorage protocols) plus the API endpoint with a local
source so we don't need git or network access.
"""

from __future__ import annotations

import tarfile
import uuid
import zipfile
from pathlib import Path

import pytest
from httpx import AsyncClient

from backend.src.core.import_sources import (
    LocalPathSource,
    TarballSource,
    _detect_language,
)
from backend.src.core.workspace_storage import LocalWorkspaceStorage


# ── Source providers ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_local_path_source_copies_files(tmp_path: Path) -> None:
    src = tmp_path / "src"
    src.mkdir()
    (src / "main.py").write_text("print('hi')")
    (src / "ignored").mkdir()
    (src / "ignored" / ".keep").write_text("")

    target = tmp_path / "target"
    metadata = await LocalPathSource(str(src)).fetch(target)

    assert metadata.workspace_dir == target
    assert metadata.files_count == 2
    assert metadata.detected_language == "python"
    assert (target / "main.py").exists()


@pytest.mark.asyncio
async def test_local_path_source_raises_for_missing(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        await LocalPathSource(str(tmp_path / "does-not-exist")).fetch(tmp_path / "target")


@pytest.mark.asyncio
async def test_tarball_source_extracts_zip(tmp_path: Path) -> None:
    archive = tmp_path / "src.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("hello.ts", "console.log('hi')")
        zf.writestr("nested/foo.ts", "")

    metadata = await TarballSource(str(archive)).fetch(tmp_path / "target")
    assert metadata.files_count == 2
    assert metadata.detected_language == "typescript"


@pytest.mark.asyncio
async def test_tarball_source_extracts_tar(tmp_path: Path) -> None:
    payload = tmp_path / "payload.go"
    payload.write_text("package main")
    archive = tmp_path / "src.tar.gz"
    with tarfile.open(archive, "w:gz") as tf:
        tf.add(payload, arcname="main.go")

    metadata = await TarballSource(str(archive)).fetch(tmp_path / "target")
    assert metadata.files_count == 1
    assert metadata.detected_language == "go"


def test_detect_language_returns_none_for_empty():
    assert _detect_language([]) is None


def test_detect_language_picks_most_common_known_extension():
    files = [Path(f"f{i}.py") for i in range(3)] + [Path("a.txt"), Path("b.md")]
    assert _detect_language(files) == "python"


# ── Workspace storage ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_local_workspace_storage_provision(tmp_path: Path) -> None:
    src = tmp_path / "src"
    src.mkdir()
    (src / "README.md").write_text("hello")

    storage = LocalWorkspaceStorage(tmp_path / "root")
    project_id = uuid.uuid4()
    location = await storage.provision(project_id, src)

    assert location.project_id == project_id
    assert (location.local_path / "README.md").exists()


@pytest.mark.asyncio
async def test_local_workspace_storage_teardown(tmp_path: Path) -> None:
    src = tmp_path / "src"
    src.mkdir()
    (src / "x.txt").write_text("")

    storage = LocalWorkspaceStorage(tmp_path / "root")
    project_id = uuid.uuid4()
    await storage.provision(project_id, src)
    await storage.teardown(project_id)
    assert not (tmp_path / "root" / str(project_id)).exists()


# ── REST endpoint ───────────────────────────────────────────────────


async def test_import_project_local_source(client: AsyncClient, tmp_path: Path, monkeypatch) -> None:
    src = tmp_path / "demo"
    src.mkdir()
    (src / "app.py").write_text("print('demo')")
    workspace_root = tmp_path / "workspaces"
    monkeypatch.setattr(
        "backend.src.api.import_project._workspace_root",
        lambda: workspace_root,
    )

    resp = await client.post(
        "/api/v1/projects/import",
        json={
            "name": "Demo",
            "description": "Imported demo",
            "source_type": "local",
            "source_uri": str(src),
            "storage_type": "local",
        },
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["files_count"] == 1
    assert body["detected_language"] == "python"
    assert body["workspace_dir"].startswith(str(workspace_root))


async def test_import_project_rejects_unknown_source(client: AsyncClient) -> None:
    resp = await client.post(
        "/api/v1/projects/import",
        json={
            "name": "Bad",
            "source_type": "wormhole",
            "source_uri": "/dev/null",
        },
    )
    # Pydantic rejects the literal before the handler runs.
    assert resp.status_code == 422


async def test_import_project_git_storage_requires_remote(client: AsyncClient, tmp_path: Path, monkeypatch) -> None:
    src = tmp_path / "src"
    src.mkdir()
    (src / "x.py").write_text("")
    monkeypatch.setattr(
        "backend.src.api.import_project._workspace_root",
        lambda: tmp_path / "ws",
    )

    resp = await client.post(
        "/api/v1/projects/import",
        json={
            "name": "Needs Remote",
            "source_type": "local",
            "source_uri": str(src),
            "storage_type": "git",
        },
    )
    assert resp.status_code == 400
    assert "storage_remote_url" in resp.json()["detail"]


async def test_import_project_seeds_analyzer_task_when_agent_present(
    client: AsyncClient, db_session, tmp_path: Path, monkeypatch
) -> None:
    """When any agent holds the ``analyze`` skill, importing a project queues a kickoff task.

    The legacy model used a dedicated ``analyzer`` role; the new model
    treats ``analyze`` as a *skill* that any agent can opt into. The
    seed-task path picks the first eligible agent in the tenant.
    """
    from datetime import datetime, timezone

    from backend.src.core.tenant_context import DEFAULT_TENANT_ID
    from backend.src.models import Agent, Task, Tenant

    db_session.add(
        Tenant(id=DEFAULT_TENANT_ID, name="Test", slug="test", owner_user_id="test-user")
    )
    await db_session.commit()
    now = datetime.now(timezone.utc)
    db_session.add(
        Agent(
            id=uuid.uuid4(),
            tenant_id=DEFAULT_TENANT_ID,
            name="CTO",
            role="cto",
            executor_type="claude_code",
            executor_config={},
            # The new model derives skills from capabilities — the
            # ``analyze`` token is what marks this agent as eligible to
            # be picked as the kickoff target.
            capabilities=["plan", "analyze", "coding"],
            status="online",
            is_active=True,
            created_at=now,
            updated_at=now,
        )
    )
    await db_session.commit()

    src = tmp_path / "demo"
    src.mkdir()
    (src / "main.py").write_text("print('hi')")
    monkeypatch.setattr(
        "backend.src.api.import_project._workspace_root",
        lambda: tmp_path / "ws",
    )

    resp = await client.post(
        "/api/v1/projects/import",
        json={
            "name": "Imported",
            "source_type": "local",
            "source_uri": str(src),
            "storage_type": "local",
        },
    )
    assert resp.status_code == 201
    project_id = uuid.UUID(resp.json()["project_id"])

    from sqlalchemy import select

    result = await db_session.execute(
        select(Task).where(Task.project_id == project_id)
    )
    tasks = list(result.scalars().all())
    assert len(tasks) == 1
    seeded = tasks[0]
    assert seeded.title == "Analyze imported codebase"
    assert seeded.task_type.value == "chore"
    assert seeded.worker_prompt is not None
    prompt_text = seeded.worker_prompt["prompt"]
    assert str(project_id) in prompt_text or seeded.description == prompt_text
    assert "python" in prompt_text.lower()


async def test_import_project_skips_seed_when_no_analyzer_agent(
    client: AsyncClient, db_session, tmp_path: Path, monkeypatch
) -> None:
    """Without an analyzer agent the import still succeeds but no task is queued."""
    src = tmp_path / "demo"
    src.mkdir()
    (src / "main.py").write_text("")
    monkeypatch.setattr(
        "backend.src.api.import_project._workspace_root",
        lambda: tmp_path / "ws2",
    )

    resp = await client.post(
        "/api/v1/projects/import",
        json={
            "name": "No Analyzer",
            "source_type": "local",
            "source_uri": str(src),
            "storage_type": "local",
        },
    )
    assert resp.status_code == 201
    project_id = uuid.UUID(resp.json()["project_id"])

    from sqlalchemy import select

    from backend.src.models import Task

    result = await db_session.execute(
        select(Task).where(Task.project_id == project_id)
    )
    assert result.scalars().all() == []
