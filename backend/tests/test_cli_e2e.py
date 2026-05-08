"""TASK-005 — `bsnexus` CLI ↔ FastAPI end-to-end smoke.

The unit-test suites mock ``build_http_client`` to a ``MagicMock`` so
sub-app logic can be exercised in isolation. This file flips that
around: it builds the real FastAPI app, overrides the auth / db
dependencies the way ``conftest.client`` does, then wraps an
``httpx.AsyncClient`` (ASGI transport) in a real
:class:`bsvibe_cli_base.CliHttpClient` and patches
``build_http_client`` in every sub-app to return that client.

What this proves end-to-end:

* ``bsnexus projects create`` round-trips through the projects router
  (FastAPI → SQLAlchemy → SQLite) and returns a 201 with a UUID.
* ``bsnexus projects list -o json`` then surfaces the row that
  ``create`` just persisted (CLI input → REST handler → DB → response
  → CLI ``OutputFormatter`` JSON).
* ``bsnexus integrations list -o json`` returns a JSON document
  whose top-level keys are the two supported providers (the redacted
  view shape — never the raw api_key).

Each command opens its own short-lived :class:`CliHttpClient`, so the
fixture rebuilds the underlying ``httpx.AsyncClient`` per invocation
to keep ASGI lifespans deterministic.
"""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport
from sqlalchemy.ext.asyncio import AsyncSession
from typer.testing import CliRunner

from backend.src.core.auth import get_current_user
from backend.src.core.tenant_context import get_tenant_id
from backend.src.storage.database import get_db

ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _strip_ansi(text: str) -> str:
    return ANSI_RE.sub("", text)


def _last_json(text: str) -> Any:
    """Extract the last top-level JSON document from ``text``.

    The backend (under TestClient) emits structlog JSON events to stdout
    while handling requests, so the CLI output can be a mix of log lines
    and the formatter's payload. The formatter's payload is always the
    last whitespace-separated JSON value, so we walk forward with
    ``raw_decode`` and keep the final parse.
    """
    decoder = json.JSONDecoder()
    idx = 0
    last: Any = None
    n = len(text)
    while idx < n:
        # Skip whitespace.
        while idx < n and text[idx].isspace():
            idx += 1
        if idx >= n:
            break
        try:
            value, end = decoder.raw_decode(text, idx)
        except json.JSONDecodeError:
            # Skip to next newline and try again.
            nl = text.find("\n", idx)
            if nl == -1:
                break
            idx = nl + 1
            continue
        last = value
        idx = end
    if last is None:
        raise json.JSONDecodeError("no JSON document found", text, 0)
    return last


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner(mix_stderr=False)


@pytest_asyncio.fixture
async def wired_app(test_app, db_session: AsyncSession, mock_user, mock_tenant_id, mock_stream_manager, seeded_tenant):
    """Mirror ``conftest.client`` but yield the configured app itself.

    We need the ASGI app (not an ``AsyncClient``) because the CLI's
    own ``CliHttpClient`` constructs its underlying ``httpx.AsyncClient``
    against an ``ASGITransport``; reusing the conftest ``client`` fixture
    would point the CLI at *its* socket instead.
    """

    async def override_get_db() -> AsyncIterator[AsyncSession]:
        yield db_session

    async def override_get_current_user() -> Any:
        return mock_user

    def override_get_tenant_id() -> uuid.UUID:
        return mock_tenant_id

    test_app.dependency_overrides[get_db] = override_get_db
    test_app.dependency_overrides[get_current_user] = override_get_current_user
    test_app.dependency_overrides[get_tenant_id] = override_get_tenant_id
    test_app.state.stream_manager = mock_stream_manager
    test_app.state.rate_limit_disabled = True

    yield test_app

    test_app.dependency_overrides.clear()


def _wire_cli_client(monkeypatch: pytest.MonkeyPatch, app) -> None:
    """Replace ``build_http_client`` in every sub-app with a TestClient-backed one."""
    from bsvibe_cli_base import CliHttpClient

    def _build(_ctx) -> CliHttpClient:
        # New httpx.AsyncClient per CLI invocation so ASGI lifespans
        # don't leak between commands. CliHttpClient.aclose() will
        # tear it down.
        async_client = httpx.AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        )
        return CliHttpClient(
            base_url="http://test",
            token="dev-token",
            http=async_client,
        )

    for module in (
        "projects",
        "requests",
        "decisions",
        "deliverables",
        "integrations",
    ):
        monkeypatch.setattr(
            f"backend.src.cli.commands.{module}.build_http_client",
            _build,
        )


def _base(*extra: str) -> list[str]:
    return ["--url", "http://test", "--token", "dev-token", "-o", "json", *extra]


def test_cli_projects_create_then_list_e2e(
    runner: CliRunner,
    wired_app,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`bsnexus projects create` persists; `bsnexus projects list` surfaces it."""
    from backend.src.cli.main import app as cli

    _wire_cli_client(monkeypatch, wired_app)

    create_result = runner.invoke(
        cli,
        _base("projects", "create", "--name", "E2E CLI Smoke", "--description", "wired through TestClient"),
    )
    assert create_result.exit_code == 0, create_result.stderr
    created = _last_json(create_result.stdout)
    assert created["name"] == "E2E CLI Smoke"
    assert "id" in created

    list_result = runner.invoke(cli, _base("projects", "list"))
    assert list_result.exit_code == 0, list_result.stderr
    rows = _last_json(list_result.stdout)
    ids = {row["id"] for row in rows}
    assert created["id"] in ids


def test_cli_integrations_list_e2e_returns_json(
    runner: CliRunner,
    wired_app,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`bsnexus integrations list -o json` returns a JSON-parsable redacted view."""
    from backend.src.cli.main import app as cli

    _wire_cli_client(monkeypatch, wired_app)

    result = runner.invoke(cli, _base("integrations", "list"))
    assert result.exit_code == 0, result.stderr
    payload = _last_json(result.stdout)
    # Tenants without a saved row default to disabled+no-key (redacted shape).
    assert "bsage" in payload
    assert "bsupervisor" in payload
    for prov in ("bsage", "bsupervisor"):
        entry = payload[prov]
        # Redacted view never carries the raw api_key.
        assert "api_key" not in entry
        assert entry["has_api_key"] is False
        assert entry["enabled"] is False


def test_cli_help_lists_all_subapps(runner: CliRunner) -> None:
    """`bsnexus --help` advertises every Phase 5a sub-app."""
    from backend.src.cli.main import app as cli

    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0, result.stderr
    out = _strip_ansi(result.stdout)
    for sub in ("projects", "requests", "decisions", "deliverables", "events", "integrations"):
        assert sub in out, f"missing sub-app: {sub}"
