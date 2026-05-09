"""``bsnexus decisions`` sub-app — wraps the decisions REST router.

Maps to ``backend.src.api.decisions`` (prefix ``/api/v1/decisions``):

* ``decisions list`` → ``GET /api/v1/decisions?project_id=&blocking_only=&limit=``
  (cross-project tenant inbox when ``--project-id`` is omitted).
* ``decisions show <id>`` → derived view: pages the list and filters
  client-side. The backend exposes the full record from the list
  response so a separate GET-by-id is unnecessary.
* ``decisions lock <id> --resolution X --resolved-by Y`` →
  ``POST /api/v1/decisions/{id}/resolve`` with the
  ``DecisionResolve`` schema (``resolution`` + optional
  ``resolved_by``). Operationally "locks in" the founder's choice.
* ``decisions unlock <id>`` → backend has no reopen endpoint today;
  the command surfaces a friendly one-line "not supported" message
  rather than silently constructing an incorrect request. Dry-run
  still renders the planned shape with ``supported=false`` so
  scripts inspecting ``--dry-run`` output can detect the gap without
  hitting the network.

``--dry-run`` skips HTTP for every command and renders the planned
request shape.
"""

from __future__ import annotations

from typing import Any

import typer

from .._client import build_http_client
from ._common import emit_dry_run, emit_http_error, run_async

app = typer.Typer(
    name="decisions",
    help="Inspect and resolve founder decisions (per-tenant inbox).",
    no_args_is_help=True,
    add_completion=False,
)

_DEFAULT_LIMIT = 50
_LIST_PATH = "/decisions"


def _list_params(project_id: str | None, blocking_only: bool, limit: int) -> dict[str, Any]:
    params: dict[str, Any] = {"limit": limit}
    if project_id is not None:
        params["project_id"] = project_id
    if blocking_only:
        params["blocking_only"] = True
    return params


@app.command("list", help="List Decisions (filter with --project-id / --blocking-only).")
def list_cmd(
    ctx: typer.Context,
    project_id: str | None = typer.Option(
        None,
        "--project-id",
        help="Filter to a single project. Omit for the tenant-wide inbox.",
    ),
    blocking_only: bool = typer.Option(
        False,
        "--blocking-only",
        help="Show only unresolved blocking decisions.",
    ),
    limit: int = typer.Option(_DEFAULT_LIMIT, "--limit", min=1, max=200),
) -> None:
    obj = ctx.obj
    params = _list_params(project_id, blocking_only, limit)

    if obj.dry_run:
        emit_dry_run(obj, {"method": "GET", "path": _LIST_PATH, "params": params})
        return

    async def _go() -> Any:
        client = build_http_client(obj)
        try:
            return await client.get(_LIST_PATH, params=params)
        finally:
            await client.aclose()

    resp = run_async(_go)
    if resp.status_code >= 400:
        emit_http_error(resp)
        raise typer.Exit(code=1)

    obj.formatter.emit(resp.json())


@app.command("show", help="Show a single Decision by id (derived from list).")
def show_cmd(
    ctx: typer.Context,
    decision_id: str = typer.Argument(..., help="Decision UUID."),
    project_id: str | None = typer.Option(
        None,
        "--project-id",
        help="Project to scope the lookup to (defaults to cross-project).",
    ),
    limit: int = typer.Option(
        200,
        "--limit",
        min=1,
        max=200,
        help="How many recent Decisions to scan when filtering.",
    ),
) -> None:
    obj = ctx.obj
    params = _list_params(project_id, blocking_only=False, limit=limit)

    if obj.dry_run:
        emit_dry_run(
            obj,
            {
                "method": "GET",
                "path": _LIST_PATH,
                "params": params,
                "filter_decision_id": decision_id,
            },
        )
        return

    async def _go() -> Any:
        client = build_http_client(obj)
        try:
            return await client.get(_LIST_PATH, params=params)
        finally:
            await client.aclose()

    resp = run_async(_go)
    if resp.status_code >= 400:
        emit_http_error(resp)
        raise typer.Exit(code=1)

    rows = resp.json() or []
    for row in rows:
        if str(row.get("id")) == decision_id:
            obj.formatter.emit(row)
            return

    typer.echo(f"Error: decision {decision_id} not found in latest {limit} entries.", err=True)
    raise typer.Exit(code=1)


@app.command("lock", help="Lock in a decision by submitting its resolution.")
def lock_cmd(
    ctx: typer.Context,
    decision_id: str = typer.Argument(..., help="Decision UUID."),
    resolution: str = typer.Option(..., "--resolution", help="Resolution string (chosen option)."),
    resolved_by: str | None = typer.Option(
        None,
        "--resolved-by",
        help="Optional founder identifier to record on the decision.",
    ),
) -> None:
    obj = ctx.obj
    body: dict[str, Any] = {"resolution": resolution}
    if resolved_by is not None:
        body["resolved_by"] = resolved_by
    path = f"/decisions/{decision_id}/resolve"

    if obj.dry_run:
        emit_dry_run(obj, {"method": "POST", "path": path, "body": body})
        return

    async def _go() -> Any:
        client = build_http_client(obj)
        try:
            return await client.post(path, json=body)
        finally:
            await client.aclose()

    resp = run_async(_go)
    if resp.status_code >= 400:
        emit_http_error(resp)
        raise typer.Exit(code=1)

    obj.formatter.emit(resp.json())


@app.command("unlock", help="Reopen a resolved decision (not supported by the backend yet).")
def unlock_cmd(
    ctx: typer.Context,
    decision_id: str = typer.Argument(..., help="Decision UUID."),
) -> None:
    obj = ctx.obj
    path = f"/decisions/{decision_id}/resolve"

    if obj.dry_run:
        emit_dry_run(
            obj,
            {
                "method": "POST",
                "path": path,
                "supported": False,
                "reason": "backend exposes no reopen endpoint",
            },
        )
        return

    typer.echo(
        "Error: 'decisions unlock' is not supported — the backend exposes no reopen endpoint.",
        err=True,
    )
    raise typer.Exit(code=1)


__all__ = ["app"]
