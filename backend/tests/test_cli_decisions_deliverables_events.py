"""TASK-004 — ``bsnexus decisions`` / ``deliverables`` / ``events`` sub-apps.

Mirrors the structure of :mod:`tests.test_cli_projects_requests`:
:class:`typer.testing.CliRunner` + ``build_http_client`` monkey-patched
to a ``MagicMock``. ANSI escapes stripped before substring matching
(Phase 3 PR #43 lesson — rich/Typer splits flag tokens across colour
escapes in the non-TTY CI path).

Coverage axes:

* sub-app wiring (subcommands listed in ``--help``),
* request shape (path / params / body),
* output shape (rendered JSON honours ``--output json``),
* ``--dry-run`` skipping HTTP across every sub-command,
* friendly error surface on 4xx (no traceback, exit code != 0),
* unsupported-by-backend stubs (``decisions unlock`` /
  ``deliverables attach``) print a clear one-line message and exit 1
  rather than crashing or silently issuing the wrong request.
* events tail uses streaming over the underlying httpx client and
  honours ``--limit`` + ``--type`` filters.
"""

from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from typer.testing import CliRunner

PROJECT_ID = "11111111-1111-1111-1111-111111111111"
DECISION_ID = "22222222-2222-2222-2222-222222222222"
DELIVERABLE_ID = "33333333-3333-3333-3333-333333333333"
TENANT = "44444444-4444-4444-4444-444444444444"

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


# ===========================================================================
# decisions sub-app
# ===========================================================================


def test_decisions_subapp_help_lists_commands(runner: CliRunner) -> None:
    from backend.src.cli.main import app

    result = runner.invoke(app, ["decisions", "--help"])
    assert result.exit_code == 0, result.stderr
    out = _strip_ansi(result.stdout)
    for sub in ("list", "show", "lock", "unlock"):
        assert sub in out


def test_decisions_list_cross_project(runner: CliRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.src.cli.main import app

    fake = _fake(monkeypatch, "decisions")
    fake.get.return_value = _resp(
        200,
        [{"id": DECISION_ID, "project_id": PROJECT_ID, "question": "ship?", "blocking": True}],
    )

    result = runner.invoke(app, _base("decisions", "list"))

    assert result.exit_code == 0, result.stderr
    fake.get.assert_awaited_once()
    args, kwargs = fake.get.await_args
    assert args[0] == "/decisions"
    params = kwargs.get("params", {})
    assert "project_id" not in params
    assert params.get("limit") == 50
    payload = json.loads(result.stdout)
    assert payload[0]["id"] == DECISION_ID


def test_decisions_list_blocking_only(runner: CliRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.src.cli.main import app

    fake = _fake(monkeypatch, "decisions")
    fake.get.return_value = _resp(200, [])
    result = runner.invoke(
        app,
        _base(
            "decisions",
            "list",
            "--project-id",
            PROJECT_ID,
            "--blocking-only",
            "--limit",
            "10",
        ),
    )

    assert result.exit_code == 0, result.stderr
    args, kwargs = fake.get.await_args
    assert args[0] == "/decisions"
    assert kwargs["params"]["project_id"] == PROJECT_ID
    assert kwargs["params"]["blocking_only"] is True
    assert kwargs["params"]["limit"] == 10


def test_decisions_list_dry_run(runner: CliRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.src.cli.main import app

    fake = _fake(monkeypatch, "decisions")
    result = runner.invoke(
        app,
        ["--url", "http://nexus.test", "--dry-run", "-o", "json", "decisions", "list"],
    )

    assert result.exit_code == 0, result.stderr
    fake.get.assert_not_awaited()
    payload = json.loads(result.stdout)
    assert payload["dry_run"] is True
    assert payload["method"] == "GET"
    assert payload["path"] == "/decisions"


def test_decisions_list_empty_table_does_not_crash(runner: CliRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.src.cli.main import app

    fake = _fake(monkeypatch, "decisions")
    fake.get.return_value = _resp(200, [])
    result = runner.invoke(
        app,
        ["--url", "http://nexus.test", "--token", "t", "-o", "table", "decisions", "list"],
    )
    assert result.exit_code == 0, result.stderr


def test_decisions_show_filters_list(runner: CliRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.src.cli.main import app

    fake = _fake(monkeypatch, "decisions")
    other = "55555555-5555-5555-5555-555555555555"
    fake.get.return_value = _resp(
        200,
        [
            {"id": other, "question": "other"},
            {"id": DECISION_ID, "question": "match"},
        ],
    )

    result = runner.invoke(app, _base("decisions", "show", DECISION_ID))

    assert result.exit_code == 0, result.stderr
    fake.get.assert_awaited_once()
    payload = json.loads(result.stdout)
    assert payload["id"] == DECISION_ID
    assert payload["question"] == "match"


def test_decisions_show_missing_returns_error(runner: CliRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.src.cli.main import app

    fake = _fake(monkeypatch, "decisions")
    fake.get.return_value = _resp(200, [])
    result = runner.invoke(app, _base("decisions", "show", DECISION_ID))

    assert result.exit_code != 0
    combined = result.stdout + result.stderr
    assert DECISION_ID in combined or "not found" in combined.lower()
    assert "Traceback" not in combined


def test_decisions_lock_posts_resolve(runner: CliRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.src.cli.main import app

    fake = _fake(monkeypatch, "decisions")
    fake.post.return_value = _resp(
        200,
        {"id": DECISION_ID, "resolution": "approved", "resolved_at": "2026-05-08T12:00:00Z"},
    )

    result = runner.invoke(
        app,
        _base(
            "decisions",
            "lock",
            DECISION_ID,
            "--resolution",
            "approved",
            "--resolved-by",
            "founder@bsvibe.dev",
        ),
    )

    assert result.exit_code == 0, result.stderr
    fake.post.assert_awaited_once()
    args, kwargs = fake.post.await_args
    assert args[0] == f"/decisions/{DECISION_ID}/resolve"
    body = kwargs["json"]
    assert body["resolution"] == "approved"
    assert body["resolved_by"] == "founder@bsvibe.dev"


def test_decisions_lock_dry_run(runner: CliRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.src.cli.main import app

    fake = _fake(monkeypatch, "decisions")
    result = runner.invoke(
        app,
        [
            "--url",
            "http://nexus.test",
            "--dry-run",
            "-o",
            "json",
            "decisions",
            "lock",
            DECISION_ID,
            "--resolution",
            "approved",
        ],
    )

    assert result.exit_code == 0, result.stderr
    fake.post.assert_not_awaited()
    payload = json.loads(result.stdout)
    assert payload["dry_run"] is True
    assert payload["method"] == "POST"
    assert payload["path"] == f"/decisions/{DECISION_ID}/resolve"
    assert payload["body"]["resolution"] == "approved"


def test_decisions_lock_403_friendly(runner: CliRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.src.cli.main import app

    fake = _fake(monkeypatch, "decisions")
    fake.post.return_value = _resp(403, {"detail": "Insufficient scope: nexus:decisions.write"})
    result = runner.invoke(
        app,
        _base("decisions", "lock", DECISION_ID, "--resolution", "approved"),
    )

    assert result.exit_code != 0
    combined = result.stdout + result.stderr
    assert "Insufficient scope" in combined
    assert "Traceback" not in combined


def test_decisions_unlock_unsupported(runner: CliRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    """Backend has no reopen endpoint — unlock prints a friendly error and exits 1."""
    from backend.src.cli.main import app

    fake = _fake(monkeypatch, "decisions")
    result = runner.invoke(app, _base("decisions", "unlock", DECISION_ID))

    assert result.exit_code != 0
    assert not fake.post.await_count
    combined = result.stdout + result.stderr
    assert "not supported" in combined.lower() or "not implemented" in combined.lower()
    assert "Traceback" not in combined


def test_decisions_unlock_dry_run_still_prints_plan(runner: CliRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    """Even in dry-run we surface that the operation is unsupported."""
    from backend.src.cli.main import app

    _fake(monkeypatch, "decisions")
    result = runner.invoke(
        app,
        [
            "--url",
            "http://nexus.test",
            "--dry-run",
            "-o",
            "json",
            "decisions",
            "unlock",
            DECISION_ID,
        ],
    )

    assert result.exit_code == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["dry_run"] is True
    assert payload.get("supported") is False


# ===========================================================================
# deliverables sub-app
# ===========================================================================


def test_deliverables_subapp_help_lists_commands(runner: CliRunner) -> None:
    from backend.src.cli.main import app

    result = runner.invoke(app, ["deliverables", "--help"])
    assert result.exit_code == 0, result.stderr
    out = _strip_ansi(result.stdout)
    for sub in ("list", "show", "attach"):
        assert sub in out


def test_deliverables_list_cross_project(runner: CliRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.src.cli.main import app

    fake = _fake(monkeypatch, "deliverables")
    fake.get.return_value = _resp(
        200,
        [{"id": DELIVERABLE_ID, "project_id": PROJECT_ID, "title": "spec.md", "status": "draft"}],
    )

    result = runner.invoke(app, _base("deliverables", "list"))

    assert result.exit_code == 0, result.stderr
    fake.get.assert_awaited_once()
    args, kwargs = fake.get.await_args
    assert args[0] == "/deliverables"
    params = kwargs.get("params", {})
    assert "project_id" not in params
    assert params.get("limit") == 50


def test_deliverables_list_scoped(runner: CliRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.src.cli.main import app

    fake = _fake(monkeypatch, "deliverables")
    fake.get.return_value = _resp(200, [])
    result = runner.invoke(
        app,
        _base("deliverables", "list", "--project-id", PROJECT_ID, "--limit", "20"),
    )

    assert result.exit_code == 0, result.stderr
    args, kwargs = fake.get.await_args
    assert args[0] == "/deliverables"
    assert kwargs["params"]["project_id"] == PROJECT_ID
    assert kwargs["params"]["limit"] == 20


def test_deliverables_list_dry_run(runner: CliRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.src.cli.main import app

    fake = _fake(monkeypatch, "deliverables")
    result = runner.invoke(
        app,
        ["--url", "http://nexus.test", "--dry-run", "-o", "json", "deliverables", "list"],
    )

    assert result.exit_code == 0, result.stderr
    fake.get.assert_not_awaited()
    payload = json.loads(result.stdout)
    assert payload["dry_run"] is True
    assert payload["path"] == "/deliverables"


def test_deliverables_list_empty_table_does_not_crash(runner: CliRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.src.cli.main import app

    fake = _fake(monkeypatch, "deliverables")
    fake.get.return_value = _resp(200, [])
    result = runner.invoke(
        app,
        ["--url", "http://nexus.test", "--token", "t", "-o", "table", "deliverables", "list"],
    )
    assert result.exit_code == 0, result.stderr


def test_deliverables_show_filters_list(runner: CliRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.src.cli.main import app

    fake = _fake(monkeypatch, "deliverables")
    other = "66666666-6666-6666-6666-666666666666"
    fake.get.return_value = _resp(
        200,
        [
            {"id": other, "title": "other"},
            {"id": DELIVERABLE_ID, "title": "match"},
        ],
    )

    result = runner.invoke(app, _base("deliverables", "show", DELIVERABLE_ID))

    assert result.exit_code == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["id"] == DELIVERABLE_ID
    assert payload["title"] == "match"


def test_deliverables_show_missing(runner: CliRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.src.cli.main import app

    fake = _fake(monkeypatch, "deliverables")
    fake.get.return_value = _resp(200, [])
    result = runner.invoke(app, _base("deliverables", "show", DELIVERABLE_ID))

    assert result.exit_code != 0
    combined = result.stdout + result.stderr
    assert DELIVERABLE_ID in combined or "not found" in combined.lower()
    assert "Traceback" not in combined


def test_deliverables_attach_unsupported(runner: CliRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    """The backend has no POST endpoint for attaching deliverables yet."""
    from backend.src.cli.main import app

    fake = _fake(monkeypatch, "deliverables")
    result = runner.invoke(
        app,
        _base("deliverables", "attach", DELIVERABLE_ID, "--path", "./report.md"),
    )

    assert result.exit_code != 0
    assert not fake.post.await_count
    combined = result.stdout + result.stderr
    assert "not supported" in combined.lower() or "not implemented" in combined.lower()
    assert "Traceback" not in combined


def test_deliverables_attach_dry_run(runner: CliRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.src.cli.main import app

    _fake(monkeypatch, "deliverables")
    result = runner.invoke(
        app,
        [
            "--url",
            "http://nexus.test",
            "--dry-run",
            "-o",
            "json",
            "deliverables",
            "attach",
            DELIVERABLE_ID,
            "--path",
            "./report.md",
        ],
    )

    assert result.exit_code == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["dry_run"] is True
    assert payload.get("supported") is False


# ===========================================================================
# events sub-app
# ===========================================================================


def test_events_subapp_help_lists_list_command(runner: CliRunner) -> None:
    from backend.src.cli.main import app

    result = runner.invoke(app, ["events", "--help"])
    assert result.exit_code == 0, result.stderr
    out = _strip_ansi(result.stdout)
    assert "list" in out


def test_events_list_dry_run(runner: CliRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.src.cli.main import app

    # No HTTP should fire; we don't even need to fake the streamer.
    result = runner.invoke(
        app,
        [
            "--url",
            "http://nexus.test",
            "--dry-run",
            "-o",
            "json",
            "events",
            "list",
            "--project-id",
            PROJECT_ID,
            "--limit",
            "5",
            "--type",
            "run_transition",
        ],
    )

    assert result.exit_code == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["dry_run"] is True
    assert payload["method"] == "GET"
    assert payload["path"] == "/events"
    assert payload["params"]["project_id"] == PROJECT_ID
    assert payload["filters"]["type"] == "run_transition"
    assert payload["filters"]["limit"] == 5


class _FakeStreamCtx:
    """Async context manager mimicking ``httpx.AsyncClient.stream``."""

    def __init__(self, status_code: int, lines: list[str]):
        self._status = status_code
        self._lines = lines

    async def __aenter__(self) -> "_FakeStreamResponse":
        return _FakeStreamResponse(self._status, self._lines)

    async def __aexit__(self, *exc_info: object) -> None:
        return None


class _FakeStreamResponse:
    def __init__(self, status_code: int, lines: list[str]):
        self.status_code = status_code
        self._lines = lines
        self._text: str | None = None

    async def aiter_lines(self) -> AsyncIterator[str]:
        for line in self._lines:
            yield line

    async def aread(self) -> bytes:  # pragma: no cover - error path body
        return b""

    @property
    def text(self) -> str:  # pragma: no cover - error path body
        return self._text or ""


def _fake_events_client(monkeypatch: pytest.MonkeyPatch, lines: list[str], status: int = 200) -> MagicMock:
    """Patch ``build_http_client`` to return a client whose ``.http.stream`` yields ``lines``."""
    inner = MagicMock(name="httpx.AsyncClient")
    inner.stream = MagicMock(return_value=_FakeStreamCtx(status, lines))
    client = MagicMock(name="CliHttpClient[events]")
    client.http = inner
    client.aclose = AsyncMock(return_value=None)
    monkeypatch.setattr("backend.src.cli.commands.events.build_http_client", lambda ctx: client)
    return client


def test_events_list_streams_and_emits(runner: CliRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.src.cli.main import app

    sse_lines = [
        "retry: 3000",
        "",
        "event: ready",
        'data: {"project_id":"' + PROJECT_ID + '"}',
        "",
        "event: run_transition",
        'data: {"run_id":"abc","to_state":"running"}',
        "",
        "event: deliverable",
        'data: {"id":"d1","status":"draft"}',
        "",
    ]
    _fake_events_client(monkeypatch, sse_lines)

    result = runner.invoke(
        app,
        _base("events", "list", "--project-id", PROJECT_ID, "--limit", "10", "--timeout", "1"),
    )

    assert result.exit_code == 0, result.stderr
    payload = json.loads(result.stdout)
    assert isinstance(payload, list)
    types = [e["event"] for e in payload]
    assert "ready" in types
    assert "run_transition" in types
    assert "deliverable" in types


def test_events_list_filters_by_type(runner: CliRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.src.cli.main import app

    sse_lines = [
        "event: ready",
        'data: {"project_id":"' + PROJECT_ID + '"}',
        "",
        "event: run_transition",
        'data: {"run_id":"abc"}',
        "",
        "event: deliverable",
        'data: {"id":"d1"}',
        "",
        "event: run_transition",
        'data: {"run_id":"def"}',
        "",
    ]
    _fake_events_client(monkeypatch, sse_lines)

    result = runner.invoke(
        app,
        _base(
            "events",
            "list",
            "--project-id",
            PROJECT_ID,
            "--type",
            "run_transition",
            "--limit",
            "10",
            "--timeout",
            "1",
        ),
    )

    assert result.exit_code == 0, result.stderr
    payload = json.loads(result.stdout)
    types = {e["event"] for e in payload}
    assert types == {"run_transition"}
    assert len(payload) == 2


def test_events_list_respects_limit(runner: CliRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.src.cli.main import app

    sse_lines: list[str] = []
    for i in range(10):
        sse_lines += ["event: run_transition", f'data: {{"run_id":"r{i}"}}', ""]
    _fake_events_client(monkeypatch, sse_lines)

    result = runner.invoke(
        app,
        _base(
            "events",
            "list",
            "--project-id",
            PROJECT_ID,
            "--limit",
            "3",
            "--timeout",
            "1",
        ),
    )

    assert result.exit_code == 0, result.stderr
    payload = json.loads(result.stdout)
    assert len(payload) == 3


def test_events_list_403_friendly(runner: CliRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.src.cli.main import app

    _fake_events_client(monkeypatch, [], status=403)

    result = runner.invoke(
        app,
        _base("events", "list", "--project-id", PROJECT_ID, "--timeout", "1"),
    )

    assert result.exit_code != 0
    combined = result.stdout + result.stderr
    assert "403" in combined
    assert "Traceback" not in combined


def test_events_list_forwards_auth_and_tenant_headers(
    runner: CliRunner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: SSE stream call MUST include Authorization + X-Tenant-Id.

    ``CliHttpClient`` only merges its stored headers via :meth:`request`;
    calling ``client.http.stream(...)`` against the underlying
    ``httpx.AsyncClient`` would skip them. Production-traffic SSE would
    then 401 against ``get_current_user`` even though the operator passed
    ``--token`` / has a token on the active profile.
    """

    captured: dict[str, object] = {}

    class _RecordingStreamCtx:
        async def __aenter__(self) -> "_FakeStreamResponse":
            return _FakeStreamResponse(200, [])

        async def __aexit__(self, *exc_info: object) -> None:
            return None

    def _record_stream(method: str, path: str, **kw: object) -> _RecordingStreamCtx:
        captured["method"] = method
        captured["path"] = path
        captured["kwargs"] = kw
        return _RecordingStreamCtx()

    inner = MagicMock(name="httpx.AsyncClient")
    inner.stream = _record_stream
    real_client = MagicMock(name="CliHttpClient[events-auth]")
    real_client.http = inner
    real_client.aclose = AsyncMock(return_value=None)
    monkeypatch.setattr(
        "backend.src.cli.commands.events.build_http_client",
        lambda ctx: real_client,
    )

    from backend.src.cli.main import app

    result = runner.invoke(
        app,
        [
            "--url",
            "http://nexus.test",
            "--token",
            "secret-token-xyz",
            "--tenant",
            TENANT,
            "-o",
            "json",
            "events",
            "list",
            "--project-id",
            PROJECT_ID,
            "--limit",
            "1",
            "--timeout",
            "0.5",
        ],
    )

    assert result.exit_code == 0, result.stderr
    headers = captured.get("kwargs", {}).get("headers")
    assert headers is not None, "stream() must receive an explicit headers kwarg"
    assert headers.get("Authorization") == "Bearer secret-token-xyz"
    assert headers.get("X-Tenant-Id") == TENANT
