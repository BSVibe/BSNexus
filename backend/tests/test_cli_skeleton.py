"""Smoke tests for the ``bsnexus`` CLI skeleton (TASK-002).

The CLI is built on :mod:`bsvibe_cli_base.cli_app`, so these tests do
not re-verify the global flag plumbing — they only assert that the
factory was wired correctly here:

* Top-level Typer app exists and exposes ``--help``.
* The six standard global flags appear in ``--help`` output (with ANSI
  escapes stripped per the Phase 3 PR #43 lesson).
* ``_client.build_http_client`` returns a configured
  :class:`CliHttpClient` from a :class:`CliContext`.
"""

from __future__ import annotations

import re

import pytest
from bsvibe_cli_base import CliContext, CliHttpClient, OutputFormatter, ProfileStore
from typer.testing import CliRunner

from backend.src.cli._client import build_http_client
from backend.src.cli.main import app

ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _strip_ansi(text: str) -> str:
    return ANSI_RE.sub("", text)


def test_app_help_renders_global_flags() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    out = _strip_ansi(result.output)
    for flag in ("--profile", "--output", "--tenant", "--token", "--url", "--dry-run"):
        assert flag in out, f"global flag {flag!r} missing from --help output:\n{out}"


def test_app_help_lists_app_name() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    out = _strip_ansi(result.output)
    assert "bsnexus" in out.lower()


@pytest.mark.parametrize(
    ("token", "expected_auth"),
    [
        ("dev-token", "Bearer dev-token"),
        (None, None),
    ],
)
def test_build_http_client_resolves_url_and_token(
    tmp_path,
    token: str | None,
    expected_auth: str | None,
) -> None:
    store = ProfileStore(path=tmp_path / "config.yaml")
    ctx = CliContext(
        profile=None,
        url="https://example.test",
        tenant_id=None,
        token=token,
        dry_run=False,
        formatter=OutputFormatter(format="json"),
        profile_store=store,
    )
    client = build_http_client(ctx)
    assert isinstance(client, CliHttpClient)
    assert client.token == token
    assert client._headers.get("Authorization") == expected_auth


def test_build_http_client_requires_url(tmp_path) -> None:
    store = ProfileStore(path=tmp_path / "config.yaml")
    ctx = CliContext(
        profile=None,
        url="",
        tenant_id=None,
        token=None,
        dry_run=False,
        formatter=OutputFormatter(format="json"),
        profile_store=store,
    )
    with pytest.raises(ValueError, match="base URL"):
        build_http_client(ctx)


def test_build_http_client_passes_tenant_header(tmp_path) -> None:
    store = ProfileStore(path=tmp_path / "config.yaml")
    ctx = CliContext(
        profile=None,
        url="https://example.test",
        tenant_id="tenant-xyz",
        token="t",
        dry_run=False,
        formatter=OutputFormatter(format="json"),
        profile_store=store,
    )
    client = build_http_client(ctx)
    assert client._headers.get("X-Tenant-Id") == "tenant-xyz"
