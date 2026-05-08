"""Shared helpers for ``bsnexus`` CLI sub-apps.

Centralises the dry-run renderer, friendly HTTP-error printer, and the
``asyncio.run`` adapter so every sub-app focuses on its own request
shape rather than re-deriving these primitives. The shape mirrors the
Phase 3 ``bsgateway.cli.commands._common`` module so both CLIs stay
recognisable to operators jumping between products.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

import typer

__all__ = [
    "emit_dry_run",
    "emit_http_error",
    "run_async",
]


def emit_dry_run(ctx_obj: Any, payload: dict[str, Any]) -> None:
    """Render the planned request without firing it."""
    ctx_obj.formatter.emit({"dry_run": True, **payload})


def emit_http_error(resp: Any) -> None:
    """Print a one-line error to stderr — no stack trace."""
    detail: Any
    try:
        body = resp.json()
    except Exception:  # pragma: no cover - defensive: body not JSON
        body = None
    if isinstance(body, dict) and "detail" in body:
        detail = body["detail"]
    elif body is not None:
        detail = body
    else:
        detail = (resp.text or "").strip()[:300]
    typer.echo(f"Error: HTTP {resp.status_code} — {detail}", err=True)


def run_async(coro_factory: Callable[[], Awaitable[Any]]) -> Any:
    """Run an async coroutine factory (for build_client + aclose pattern)."""
    return asyncio.run(coro_factory())
