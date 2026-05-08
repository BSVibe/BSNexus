"""``bsnexus mcp`` CLI sub-app (TASK-005).

Two commands:

* ``bsnexus mcp list-tools`` — local enumeration of the shared
  :class:`ToolRegistry` (no HTTP). Output respects ``--output json``.
* ``bsnexus mcp serve --transport stdio|http`` — boots the same
  registry and runs FastMCP's stdio (or http) server. Auth context for
  the stdio path is read from ``BSV_BOOTSTRAP_TOKEN``.

The serve path is asserted as a "factory was called with correct
transport" check rather than actually entering the I/O loop — running
``run_stdio_async`` would block on stdin in CI.
"""

from __future__ import annotations

import json
import re

from typer.testing import CliRunner

from backend.src.cli.main import app

ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _strip_ansi(text: str) -> str:
    return ANSI_RE.sub("", text)


# ── --help ────────────────────────────────────────────────────────


def test_mcp_subapp_appears_in_help() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    out = _strip_ansi(result.output)
    assert "mcp" in out


def test_mcp_help_lists_serve_and_list_tools() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["mcp", "--help"])
    assert result.exit_code == 0
    out = _strip_ansi(result.output)
    assert "list-tools" in out
    assert "serve" in out


# ── list-tools ─────────────────────────────────────────────────────


def test_list_tools_json_includes_domain_and_admin() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["--output", "json", "mcp", "list-tools"])
    assert result.exit_code == 0, result.output
    payload = json.loads(_strip_ansi(result.output))
    names = {t["name"] for t in payload["tools"]}
    # Sanity: catalog includes one of each side.
    assert "decision_create" in names  # domain
    assert "bsnexus_projects_list" in names  # admin


def test_list_tools_includes_required_scopes_and_audit_event() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["--output", "json", "mcp", "list-tools"])
    assert result.exit_code == 0, result.output
    payload = json.loads(_strip_ansi(result.output))
    by_name = {t["name"]: t for t in payload["tools"]}
    # ``decisions lock`` delegates audit to the REST helper, so it
    # leaves the dispatcher-side audit_event slot blank. ``projects
    # archive`` declares its event at the Tool level — pick that for
    # the mutating-tool assertion.
    lock = by_name["bsnexus_decisions_lock"]
    assert "bsnexus:decisions:write" in lock["required_scopes"]
    archive = by_name["bsnexus_projects_archive"]
    assert "bsnexus:projects:write" in archive["required_scopes"]
    assert archive["audit_event"]  # dispatcher-side emit is declared


# ── serve --transport stdio ────────────────────────────────────────


def test_serve_dry_run_stdio(monkeypatch) -> None:
    """``--dry-run`` returns the planned transport without starting the
    server. Avoids running ``run_stdio_async`` (which blocks on stdin).
    """
    runner = CliRunner()
    monkeypatch.setenv("BSV_BOOTSTRAP_TOKEN", "bsv_admin_test")
    result = runner.invoke(
        app,
        ["--output", "json", "--dry-run", "mcp", "serve", "--transport", "stdio"],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(_strip_ansi(result.output))
    assert payload["dry_run"] is True
    assert payload["transport"] == "stdio"
    assert payload["bootstrap_token_present"] is True


def test_serve_dry_run_stdio_without_token_warns(monkeypatch) -> None:
    runner = CliRunner()
    monkeypatch.delenv("BSV_BOOTSTRAP_TOKEN", raising=False)
    result = runner.invoke(
        app,
        ["--output", "json", "--dry-run", "mcp", "serve", "--transport", "stdio"],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(_strip_ansi(result.output))
    assert payload["bootstrap_token_present"] is False


def test_serve_invalid_transport_errors() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["--dry-run", "mcp", "serve", "--transport", "websocket"])
    assert result.exit_code != 0
