"""TASK-005 — ``bsnexus integrations`` sub-app.

Mirrors :mod:`tests.test_cli_decisions_deliverables_events`: CliRunner +
``build_http_client`` monkey-patched to a ``MagicMock``. ANSI escapes
stripped before substring matching (Phase 3 PR #43 lesson).

Coverage axes:

* sub-app wiring (subcommands listed in ``--help``),
* request shape (path / params / body),
* output shape (rendered JSON honours ``--output json``),
* ``--dry-run`` skipping HTTP across every sub-command,
* friendly error surface on 4xx (no traceback, exit code != 0),
* invalid provider rejected early,
* ``--api-key`` redacted in dry-run output (never printed verbatim).
"""

from __future__ import annotations

import json
import re
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from typer.testing import CliRunner

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


def _fake(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    client = MagicMock(name="CliHttpClient[integrations]")
    client.aclose = AsyncMock(return_value=None)
    client.get = AsyncMock()
    client.post = AsyncMock()
    client.patch = AsyncMock()
    monkeypatch.setattr("backend.src.cli.commands.integrations.build_http_client", lambda ctx: client)
    return client


def _base(*extra: str) -> list[str]:
    return ["--url", "http://nexus.test", "--token", "tok", "-o", "json", *extra]


# ===========================================================================
# integrations sub-app — help wiring
# ===========================================================================


def test_integrations_subapp_help_lists_commands(runner: CliRunner) -> None:
    from backend.src.cli.main import app

    result = runner.invoke(app, ["integrations", "--help"])
    assert result.exit_code == 0, result.stderr
    out = _strip_ansi(result.stdout)
    for sub in ("list", "add", "remove", "test"):
        assert sub in out


# ===========================================================================
# list
# ===========================================================================


def test_integrations_list_calls_get(runner: CliRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.src.cli.main import app

    fake = _fake(monkeypatch)
    fake.get.return_value = _resp(
        200,
        {
            "bsage": {
                "provider": "bsage",
                "enabled": True,
                "base_url": "https://bsage.test",
                "has_api_key": True,
                "extra_config": {},
            },
            "bsupervisor": {
                "provider": "bsupervisor",
                "enabled": False,
                "base_url": None,
                "has_api_key": False,
                "extra_config": {},
            },
        },
    )

    result = runner.invoke(app, _base("integrations", "list"))

    assert result.exit_code == 0, result.stderr
    fake.get.assert_awaited_once()
    args, _ = fake.get.await_args
    assert args[0] == "/api/v1/integrations"
    payload = json.loads(result.stdout)
    assert payload["bsage"]["has_api_key"] is True


def test_integrations_list_dry_run(runner: CliRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.src.cli.main import app

    fake = _fake(monkeypatch)
    result = runner.invoke(
        app,
        ["--url", "http://nexus.test", "--dry-run", "-o", "json", "integrations", "list"],
    )

    assert result.exit_code == 0, result.stderr
    fake.get.assert_not_awaited()
    payload = json.loads(result.stdout)
    assert payload["dry_run"] is True
    assert payload["method"] == "GET"
    assert payload["path"] == "/api/v1/integrations"


def test_integrations_list_empty_table_does_not_crash(runner: CliRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.src.cli.main import app

    fake = _fake(monkeypatch)
    fake.get.return_value = _resp(200, {})
    result = runner.invoke(
        app,
        ["--url", "http://nexus.test", "--token", "t", "-o", "table", "integrations", "list"],
    )
    assert result.exit_code == 0, result.stderr


# ===========================================================================
# add (PATCH upsert)
# ===========================================================================


def test_integrations_add_patches_provider(runner: CliRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.src.cli.main import app

    fake = _fake(monkeypatch)
    fake.patch.return_value = _resp(
        200,
        {
            "provider": "bsage",
            "enabled": True,
            "base_url": "https://bsage.test",
            "has_api_key": True,
            "extra_config": {},
        },
    )

    result = runner.invoke(
        app,
        _base(
            "integrations",
            "add",
            "bsage",
            "--base-url",
            "https://bsage.test",
            "--api-key",
            "secret-key",
            "--enabled",
        ),
    )

    assert result.exit_code == 0, result.stderr
    fake.patch.assert_awaited_once()
    args, kwargs = fake.patch.await_args
    assert args[0] == "/api/v1/integrations/bsage"
    body = kwargs["json"]
    assert body["enabled"] is True
    assert body["base_url"] == "https://bsage.test"
    assert body["api_key"] == "secret-key"


def test_integrations_add_invalid_provider_rejected(runner: CliRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.src.cli.main import app

    fake = _fake(monkeypatch)
    result = runner.invoke(
        app,
        _base(
            "integrations",
            "add",
            "not-a-provider",
            "--base-url",
            "https://x.test",
        ),
    )
    assert result.exit_code != 0
    fake.patch.assert_not_awaited()
    combined = _strip_ansi(result.stdout + result.stderr)
    assert "provider" in combined.lower()


def test_integrations_add_dry_run_redacts_api_key(runner: CliRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.src.cli.main import app

    fake = _fake(monkeypatch)
    secret = "super-secret-key-shhh"
    result = runner.invoke(
        app,
        [
            "--url",
            "http://nexus.test",
            "--dry-run",
            "-o",
            "json",
            "integrations",
            "add",
            "bsupervisor",
            "--base-url",
            "https://bsup.test",
            "--api-key",
            secret,
            "--enabled",
        ],
    )

    assert result.exit_code == 0, result.stderr
    fake.patch.assert_not_awaited()
    # The literal secret MUST NOT appear in dry-run output.
    assert secret not in result.stdout
    payload = json.loads(result.stdout)
    assert payload["dry_run"] is True
    assert payload["method"] == "PATCH"
    assert payload["path"] == "/api/v1/integrations/bsupervisor"
    assert payload["body"]["api_key"] == "***"
    assert payload["body"]["base_url"] == "https://bsup.test"
    assert payload["body"]["enabled"] is True


def test_integrations_add_403_friendly(runner: CliRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.src.cli.main import app

    fake = _fake(monkeypatch)
    fake.patch.return_value = _resp(403, {"detail": "Insufficient scope: nexus:integrations.write"})

    result = runner.invoke(
        app,
        _base(
            "integrations",
            "add",
            "bsage",
            "--base-url",
            "https://bsage.test",
        ),
    )

    assert result.exit_code != 0
    combined = result.stdout + result.stderr
    assert "Insufficient scope" in combined
    assert "Traceback" not in combined


def test_integrations_add_omits_unset_fields(runner: CliRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    """Only fields the operator explicitly set should land in the PATCH body."""
    from backend.src.cli.main import app

    fake = _fake(monkeypatch)
    fake.patch.return_value = _resp(
        200,
        {
            "provider": "bsage",
            "enabled": True,
            "base_url": "https://bsage.test",
            "has_api_key": False,
            "extra_config": {},
        },
    )

    result = runner.invoke(
        app,
        _base("integrations", "add", "bsage", "--base-url", "https://bsage.test"),
    )

    assert result.exit_code == 0, result.stderr
    args, kwargs = fake.patch.await_args
    body = kwargs["json"]
    assert body == {"base_url": "https://bsage.test"}


# ===========================================================================
# remove (PATCH clear)
# ===========================================================================


def test_integrations_remove_disables_and_clears(runner: CliRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.src.cli.main import app

    fake = _fake(monkeypatch)
    fake.patch.return_value = _resp(
        200,
        {
            "provider": "bsage",
            "enabled": False,
            "base_url": None,
            "has_api_key": False,
            "extra_config": {},
        },
    )

    result = runner.invoke(app, _base("integrations", "remove", "bsage"))

    assert result.exit_code == 0, result.stderr
    fake.patch.assert_awaited_once()
    args, kwargs = fake.patch.await_args
    assert args[0] == "/api/v1/integrations/bsage"
    body = kwargs["json"]
    assert body["enabled"] is False
    assert body["api_key"] is None
    assert body["base_url"] is None


def test_integrations_remove_dry_run(runner: CliRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.src.cli.main import app

    fake = _fake(monkeypatch)
    result = runner.invoke(
        app,
        [
            "--url",
            "http://nexus.test",
            "--dry-run",
            "-o",
            "json",
            "integrations",
            "remove",
            "bsupervisor",
        ],
    )

    assert result.exit_code == 0, result.stderr
    fake.patch.assert_not_awaited()
    payload = json.loads(result.stdout)
    assert payload["method"] == "PATCH"
    assert payload["path"] == "/api/v1/integrations/bsupervisor"
    assert payload["body"]["enabled"] is False


def test_integrations_remove_invalid_provider(runner: CliRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.src.cli.main import app

    fake = _fake(monkeypatch)
    result = runner.invoke(app, _base("integrations", "remove", "garbage"))
    assert result.exit_code != 0
    fake.patch.assert_not_awaited()


# ===========================================================================
# test (POST /test connectivity probe)
# ===========================================================================


def test_integrations_test_calls_probe(runner: CliRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.src.cli.main import app

    fake = _fake(monkeypatch)
    fake.post.return_value = _resp(200, {"ok": True, "status": "healthy", "detail": None})

    result = runner.invoke(app, _base("integrations", "test", "bsage"))

    assert result.exit_code == 0, result.stderr
    fake.post.assert_awaited_once()
    args, _ = fake.post.await_args
    assert args[0] == "/api/v1/integrations/bsage/test"
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["status"] == "healthy"


def test_integrations_test_unhealthy_exit_nonzero(runner: CliRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    """Probe returning 200 but ``ok=false`` is still operator-visible failure."""
    from backend.src.cli.main import app

    fake = _fake(monkeypatch)
    fake.post.return_value = _resp(
        200,
        {"ok": False, "status": "unauthorized", "detail": "HTTP 401"},
    )

    result = runner.invoke(app, _base("integrations", "test", "bsage"))

    # We still emit the JSON (operator wants to see the detail), but exit
    # non-zero so scripted callers can branch on success.
    assert result.exit_code != 0
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert payload["status"] == "unauthorized"


def test_integrations_test_dry_run(runner: CliRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.src.cli.main import app

    fake = _fake(monkeypatch)
    result = runner.invoke(
        app,
        ["--url", "http://nexus.test", "--dry-run", "-o", "json", "integrations", "test", "bsage"],
    )

    assert result.exit_code == 0, result.stderr
    fake.post.assert_not_awaited()
    payload = json.loads(result.stdout)
    assert payload["method"] == "POST"
    assert payload["path"] == "/api/v1/integrations/bsage/test"


def test_integrations_test_4xx_friendly(runner: CliRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.src.cli.main import app

    fake = _fake(monkeypatch)
    fake.post.return_value = _resp(404, {"detail": "Not Found"})

    result = runner.invoke(app, _base("integrations", "test", "bsage"))

    assert result.exit_code != 0
    combined = result.stdout + result.stderr
    assert "Not Found" in combined
    assert "Traceback" not in combined


def test_integrations_test_invalid_provider(runner: CliRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.src.cli.main import app

    fake = _fake(monkeypatch)
    result = runner.invoke(app, _base("integrations", "test", "no-such"))
    assert result.exit_code != 0
    fake.post.assert_not_awaited()
