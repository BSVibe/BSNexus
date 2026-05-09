"""TASK-003 — ``bsnexus projects`` + ``bsnexus requests`` CLI sub-apps.

Each sub-app is exercised through :class:`typer.testing.CliRunner` with
:func:`backend.src.cli.commands.projects.build_http_client` /
``…requests.build_http_client`` monkey-patched to a ``MagicMock``. Tests
cover:

* sub-app wiring (subcommands registered + reachable via ``--help``),
* request shape (path / params / body on the mocked client),
* output shape (rendered JSON honours ``--output json``),
* ``--dry-run`` truly skipping HTTP,
* friendly error surface on 4xx (no traceback, exit code != 0).

Per the Phase 3 PR #43 lesson, all help-text assertions strip ANSI
escapes — rich/Typer splits flag tokens across colour escapes in the
non-TTY CI path so plain substring matches against ``result.stdout``
fail otherwise.
"""

from __future__ import annotations

import json
import re
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from typer.testing import CliRunner

PROJECT_ID = "11111111-1111-1111-1111-111111111111"
REQUEST_ID = "22222222-2222-2222-2222-222222222222"
TENANT = "33333333-3333-3333-3333-333333333333"

ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _strip_ansi(text: str) -> str:
    return ANSI_RE.sub("", text)


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner(mix_stderr=False)


def _resp(status_code: int = 200, payload: object | None = None) -> MagicMock:
    r = MagicMock(spec=httpx.Response)
    r.status_code = status_code
    r.json.return_value = payload
    r.text = json.dumps(payload, default=str) if payload is not None else ""
    return r


def _fake(monkeypatch: pytest.MonkeyPatch, module: str) -> MagicMock:
    client = MagicMock(name=f"CliHttpClient[{module}]")
    client.aclose = AsyncMock(return_value=None)
    client.get = AsyncMock()
    client.post = AsyncMock()
    client.delete = AsyncMock()
    client.patch = AsyncMock()
    monkeypatch.setattr(f"backend.src.cli.commands.{module}.build_http_client", lambda ctx: client)
    return client


def _base(*extra: str) -> list[str]:
    args = ["--url", "http://nexus.test", "--token", "tok", "-o", "json"]
    args += list(extra)
    return args


# ---------------------------------------------------------------------------
# projects sub-app — help wiring
# ---------------------------------------------------------------------------


def test_projects_subapp_help_lists_commands(runner: CliRunner) -> None:
    from backend.src.cli.main import app

    result = runner.invoke(app, ["projects", "--help"])
    assert result.exit_code == 0, result.stderr
    out = _strip_ansi(result.stdout)
    for sub in ("list", "show", "create", "archive"):
        assert sub in out


# ---------------------------------------------------------------------------
# projects list
# ---------------------------------------------------------------------------


def test_projects_list_emits_json(runner: CliRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.src.cli.main import app

    fake = _fake(monkeypatch, "projects")
    fake.get.return_value = _resp(200, [{"id": PROJECT_ID, "tenant_id": TENANT, "name": "demo"}])

    result = runner.invoke(app, _base("projects", "list"))

    assert result.exit_code == 0, result.stderr
    fake.get.assert_awaited_once_with("/projects")
    payload = json.loads(result.stdout)
    assert payload[0]["id"] == PROJECT_ID
    assert payload[0]["name"] == "demo"


def test_projects_list_dry_run_skips_http(runner: CliRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.src.cli.main import app

    fake = _fake(monkeypatch, "projects")
    result = runner.invoke(
        app,
        ["--url", "http://nexus.test", "--dry-run", "-o", "json", "projects", "list"],
    )

    assert result.exit_code == 0, result.stderr
    fake.get.assert_not_awaited()
    payload = json.loads(result.stdout)
    assert payload["dry_run"] is True
    assert payload["method"] == "GET"
    assert payload["path"] == "/projects"


def test_projects_list_empty_table_does_not_crash(runner: CliRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    """Phase 3 lesson — empty table render must not raise."""
    from backend.src.cli.main import app

    fake = _fake(monkeypatch, "projects")
    fake.get.return_value = _resp(200, [])
    result = runner.invoke(app, ["--url", "http://nexus.test", "--token", "t", "-o", "table", "projects", "list"])
    assert result.exit_code == 0, result.stderr


# ---------------------------------------------------------------------------
# projects show
# ---------------------------------------------------------------------------


def test_projects_show(runner: CliRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.src.cli.main import app

    fake = _fake(monkeypatch, "projects")
    fake.get.return_value = _resp(200, {"id": PROJECT_ID, "name": "demo"})

    result = runner.invoke(app, _base("projects", "show", PROJECT_ID))

    assert result.exit_code == 0, result.stderr
    fake.get.assert_awaited_once_with(f"/projects/{PROJECT_ID}")
    assert json.loads(result.stdout)["id"] == PROJECT_ID


def test_projects_show_404_friendly(runner: CliRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.src.cli.main import app

    fake = _fake(monkeypatch, "projects")
    fake.get.return_value = _resp(404, {"detail": "Project not found"})
    result = runner.invoke(app, _base("projects", "show", PROJECT_ID))

    assert result.exit_code != 0
    combined = result.stdout + result.stderr
    assert "Project not found" in combined
    assert "Traceback" not in combined


# ---------------------------------------------------------------------------
# projects create
# ---------------------------------------------------------------------------


def test_projects_create_posts_payload(runner: CliRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.src.cli.main import app

    fake = _fake(monkeypatch, "projects")
    fake.post.return_value = _resp(201, {"id": PROJECT_ID, "name": "Acme"})

    result = runner.invoke(
        app,
        _base(
            "projects",
            "create",
            "--name",
            "Acme",
            "--description",
            "demo project",
        ),
    )

    assert result.exit_code == 0, result.stderr
    fake.post.assert_awaited_once()
    args, kwargs = fake.post.await_args
    assert args[0] == "/projects"
    body = kwargs["json"]
    assert body["name"] == "Acme"
    assert body["description"] == "demo project"
    payload = json.loads(result.stdout)
    assert payload["id"] == PROJECT_ID


def test_projects_create_dry_run(runner: CliRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.src.cli.main import app

    fake = _fake(monkeypatch, "projects")
    result = runner.invoke(
        app,
        [
            "--url",
            "http://nexus.test",
            "--dry-run",
            "-o",
            "json",
            "projects",
            "create",
            "--name",
            "Acme",
        ],
    )

    assert result.exit_code == 0, result.stderr
    fake.post.assert_not_awaited()
    payload = json.loads(result.stdout)
    assert payload["dry_run"] is True
    assert payload["method"] == "POST"
    assert payload["path"] == "/projects"
    assert payload["body"]["name"] == "Acme"


# ---------------------------------------------------------------------------
# projects archive
# ---------------------------------------------------------------------------


def test_projects_archive(runner: CliRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.src.cli.main import app

    fake = _fake(monkeypatch, "projects")
    fake.delete.return_value = _resp(204, None)
    result = runner.invoke(app, _base("projects", "archive", PROJECT_ID))

    assert result.exit_code == 0, result.stderr
    fake.delete.assert_awaited_once_with(f"/projects/{PROJECT_ID}")


def test_projects_archive_dry_run(runner: CliRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.src.cli.main import app

    fake = _fake(monkeypatch, "projects")
    result = runner.invoke(
        app,
        [
            "--url",
            "http://nexus.test",
            "--dry-run",
            "-o",
            "json",
            "projects",
            "archive",
            PROJECT_ID,
        ],
    )

    assert result.exit_code == 0, result.stderr
    fake.delete.assert_not_awaited()
    payload = json.loads(result.stdout)
    assert payload["dry_run"] is True
    assert payload["method"] == "DELETE"
    assert payload["path"] == f"/projects/{PROJECT_ID}"


# ---------------------------------------------------------------------------
# requests sub-app — help + list
# ---------------------------------------------------------------------------


def test_requests_subapp_help_lists_commands(runner: CliRunner) -> None:
    from backend.src.cli.main import app

    result = runner.invoke(app, ["requests", "--help"])
    assert result.exit_code == 0, result.stderr
    out = _strip_ansi(result.stdout)
    for sub in ("list", "show", "create", "update"):
        assert sub in out


def test_requests_list_cross_project(runner: CliRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.src.cli.main import app

    fake = _fake(monkeypatch, "requests")
    fake.get.return_value = _resp(
        200,
        [
            {"id": REQUEST_ID, "project_id": PROJECT_ID, "intent_summary": "do thing"},
        ],
    )

    result = runner.invoke(app, _base("requests", "list"))

    assert result.exit_code == 0, result.stderr
    fake.get.assert_awaited_once()
    args, kwargs = fake.get.await_args
    assert args[0] == "/requests"
    params = kwargs.get("params", {})
    assert "project_id" not in params
    assert params.get("limit") == 50


def test_requests_list_scoped_to_project(runner: CliRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.src.cli.main import app

    fake = _fake(monkeypatch, "requests")
    fake.get.return_value = _resp(200, [])
    result = runner.invoke(
        app,
        _base(
            "requests",
            "list",
            "--project-id",
            PROJECT_ID,
            "--limit",
            "20",
        ),
    )

    assert result.exit_code == 0, result.stderr
    args, kwargs = fake.get.await_args
    assert args[0] == "/requests"
    assert kwargs["params"]["project_id"] == PROJECT_ID
    assert kwargs["params"]["limit"] == 20


def test_requests_list_dry_run(runner: CliRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.src.cli.main import app

    fake = _fake(monkeypatch, "requests")
    result = runner.invoke(
        app,
        [
            "--url",
            "http://nexus.test",
            "--dry-run",
            "-o",
            "json",
            "requests",
            "list",
            "--project-id",
            PROJECT_ID,
        ],
    )

    assert result.exit_code == 0, result.stderr
    fake.get.assert_not_awaited()
    payload = json.loads(result.stdout)
    assert payload["dry_run"] is True
    assert payload["method"] == "GET"
    assert payload["path"] == "/requests"
    assert payload["params"]["project_id"] == PROJECT_ID


# ---------------------------------------------------------------------------
# requests show — derived view (filter by id from list)
# ---------------------------------------------------------------------------


def test_requests_show_filters_list(runner: CliRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.src.cli.main import app

    fake = _fake(monkeypatch, "requests")
    other_id = "44444444-4444-4444-4444-444444444444"
    fake.get.return_value = _resp(
        200,
        [
            {"id": other_id, "intent_summary": "other"},
            {"id": REQUEST_ID, "intent_summary": "match"},
        ],
    )

    result = runner.invoke(app, _base("requests", "show", REQUEST_ID))

    assert result.exit_code == 0, result.stderr
    fake.get.assert_awaited_once()
    payload = json.loads(result.stdout)
    assert payload["id"] == REQUEST_ID
    assert payload["intent_summary"] == "match"


def test_requests_show_missing_returns_error(runner: CliRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.src.cli.main import app

    fake = _fake(monkeypatch, "requests")
    fake.get.return_value = _resp(200, [])
    result = runner.invoke(app, _base("requests", "show", REQUEST_ID))

    assert result.exit_code != 0
    combined = result.stdout + result.stderr
    assert REQUEST_ID in combined or "not found" in combined.lower()
    assert "Traceback" not in combined


# ---------------------------------------------------------------------------
# requests create — POST /api/v1/messages
# ---------------------------------------------------------------------------


def test_requests_create_posts_message(runner: CliRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.src.cli.main import app

    fake = _fake(monkeypatch, "requests")
    fake.post.return_value = _resp(
        201,
        {"request_id": REQUEST_ID, "request_created": True, "intent": "request"},
    )

    result = runner.invoke(
        app,
        _base(
            "requests",
            "create",
            "--project-id",
            PROJECT_ID,
            "--content",
            "ship it",
        ),
    )

    assert result.exit_code == 0, result.stderr
    fake.post.assert_awaited_once()
    args, kwargs = fake.post.await_args
    assert args[0] == "/messages"
    body = kwargs["json"]
    assert body["project_id"] == PROJECT_ID
    assert body["content"] == "ship it"


def test_requests_create_dry_run(runner: CliRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.src.cli.main import app

    fake = _fake(monkeypatch, "requests")
    result = runner.invoke(
        app,
        [
            "--url",
            "http://nexus.test",
            "--dry-run",
            "-o",
            "json",
            "requests",
            "create",
            "--project-id",
            PROJECT_ID,
            "--content",
            "ship it",
        ],
    )

    assert result.exit_code == 0, result.stderr
    fake.post.assert_not_awaited()
    payload = json.loads(result.stdout)
    assert payload["dry_run"] is True
    assert payload["method"] == "POST"
    assert payload["path"] == "/messages"
    assert payload["body"]["project_id"] == PROJECT_ID
    assert payload["body"]["content"] == "ship it"


# ---------------------------------------------------------------------------
# requests update — same /messages endpoint, modification path
# ---------------------------------------------------------------------------


def test_requests_update_posts_message(runner: CliRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.src.cli.main import app

    fake = _fake(monkeypatch, "requests")
    fake.post.return_value = _resp(201, {"request_id": REQUEST_ID, "request_created": False, "intent": "request"})

    result = runner.invoke(
        app,
        _base(
            "requests",
            "update",
            "--project-id",
            PROJECT_ID,
            "--content",
            "actually use postgres",
        ),
    )

    assert result.exit_code == 0, result.stderr
    args, kwargs = fake.post.await_args
    assert args[0] == "/messages"
    body = kwargs["json"]
    assert body["project_id"] == PROJECT_ID
    assert body["content"] == "actually use postgres"
