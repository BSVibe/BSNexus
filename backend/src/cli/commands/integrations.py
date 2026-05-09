"""``bsnexus integrations`` sub-app — wraps the integrations REST router.

Maps to ``backend.src.api.integrations`` (prefix ``/api/v1/integrations``):

* ``integrations list`` → ``GET /api/v1/integrations`` (returns the
  redacted view of every provider; raw api_keys never cross the wire).
* ``integrations add <provider> [--base-url …] [--api-key …] [--enabled]``
  → ``PATCH /api/v1/integrations/{provider}`` (upsert). Only fields the
  operator explicitly set are sent so the backend's ``exclude_unset``
  semantics work (an unset ``enabled`` doesn't accidentally toggle).
* ``integrations remove <provider>`` → ``PATCH`` clear shape
  ``{enabled: false, base_url: null, api_key: null}``.
* ``integrations test <provider>`` → ``POST /api/v1/integrations/{provider}/test``
  (connectivity + auth probe). Exit code is non-zero when the server
  returns ``ok=false`` so scripts can branch on success.

API-key handling — ``--api-key`` is a free-form secret. It is NEVER
logged, NEVER printed verbatim by the CLI. Dry-run renders ``api_key:
"***"`` so operators can verify the request shape without leaking the
secret to scrollback / shell history / CI logs.

``--dry-run`` skips HTTP for every command.
"""

from __future__ import annotations

from typing import Any

import typer

from .._client import build_http_client
from ._common import emit_dry_run, emit_http_error, run_async

app = typer.Typer(
    name="integrations",
    help="Manage per-tenant integrations (BSage, BSupervisor).",
    no_args_is_help=True,
    add_completion=False,
)

_LIST_PATH = "/integrations"
_VALID_PROVIDERS = ("bsage", "bsupervisor")


def _normalize_provider(provider: str) -> str:
    if provider not in _VALID_PROVIDERS:
        typer.echo(
            f"Error: unknown provider '{provider}'. Expected one of: {', '.join(_VALID_PROVIDERS)}.",
            err=True,
        )
        raise typer.Exit(code=2)
    return provider


def _redact_body(body: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of ``body`` with ``api_key`` masked for display."""
    if "api_key" in body and body["api_key"] is not None:
        return {**body, "api_key": "***"}
    return body


@app.command("list", help="List all per-tenant integrations (redacted).")
def list_cmd(ctx: typer.Context) -> None:
    obj = ctx.obj

    if obj.dry_run:
        emit_dry_run(obj, {"method": "GET", "path": _LIST_PATH})
        return

    async def _go() -> Any:
        client = build_http_client(obj)
        try:
            return await client.get(_LIST_PATH)
        finally:
            await client.aclose()

    resp = run_async(_go)
    if resp.status_code >= 400:
        emit_http_error(resp)
        raise typer.Exit(code=1)

    obj.formatter.emit(resp.json())


@app.command("add", help="Upsert an integration's config (PATCH /integrations/<provider>).")
def add_cmd(
    ctx: typer.Context,
    provider: str = typer.Argument(..., help=f"Provider name. One of: {', '.join(_VALID_PROVIDERS)}."),
    base_url: str | None = typer.Option(None, "--base-url", help="Service base URL."),
    api_key: str | None = typer.Option(
        None,
        "--api-key",
        help="API key (encrypted at rest by the backend; redacted in dry-run output).",
    ),
    enabled: bool | None = typer.Option(
        None,
        "--enabled/--disabled",
        help="Toggle the integration on/off. Omit to leave the current value untouched.",
    ),
) -> None:
    obj = ctx.obj
    prov = _normalize_provider(provider)
    path = f"/integrations/{prov}"

    body: dict[str, Any] = {}
    if base_url is not None:
        body["base_url"] = base_url
    if api_key is not None:
        body["api_key"] = api_key
    if enabled is not None:
        body["enabled"] = enabled

    if obj.dry_run:
        emit_dry_run(obj, {"method": "PATCH", "path": path, "body": _redact_body(body)})
        return

    async def _go() -> Any:
        client = build_http_client(obj)
        try:
            return await client.patch(path, json=body)
        finally:
            await client.aclose()

    resp = run_async(_go)
    if resp.status_code >= 400:
        emit_http_error(resp)
        raise typer.Exit(code=1)

    obj.formatter.emit(resp.json())


@app.command("remove", help="Disable an integration and clear its credentials.")
def remove_cmd(
    ctx: typer.Context,
    provider: str = typer.Argument(..., help=f"Provider name. One of: {', '.join(_VALID_PROVIDERS)}."),
) -> None:
    obj = ctx.obj
    prov = _normalize_provider(provider)
    path = f"/integrations/{prov}"
    body: dict[str, Any] = {"enabled": False, "base_url": None, "api_key": None}

    if obj.dry_run:
        emit_dry_run(obj, {"method": "PATCH", "path": path, "body": body})
        return

    async def _go() -> Any:
        client = build_http_client(obj)
        try:
            return await client.patch(path, json=body)
        finally:
            await client.aclose()

    resp = run_async(_go)
    if resp.status_code >= 400:
        emit_http_error(resp)
        raise typer.Exit(code=1)

    obj.formatter.emit(resp.json())


@app.command("test", help="Run a connectivity + auth probe against the configured integration.")
def test_cmd(
    ctx: typer.Context,
    provider: str = typer.Argument(..., help=f"Provider name. One of: {', '.join(_VALID_PROVIDERS)}."),
) -> None:
    obj = ctx.obj
    prov = _normalize_provider(provider)
    path = f"/integrations/{prov}/test"

    if obj.dry_run:
        emit_dry_run(obj, {"method": "POST", "path": path})
        return

    async def _go() -> Any:
        client = build_http_client(obj)
        try:
            return await client.post(path)
        finally:
            await client.aclose()

    resp = run_async(_go)
    if resp.status_code >= 400:
        emit_http_error(resp)
        raise typer.Exit(code=1)

    payload = resp.json()
    obj.formatter.emit(payload)
    if isinstance(payload, dict) and payload.get("ok") is False:
        raise typer.Exit(code=1)


__all__ = ["app"]
