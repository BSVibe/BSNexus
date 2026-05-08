"""``bsnexus deliverables`` sub-app — wraps the deliverables REST router.

Maps to ``backend.src.api.deliverables`` (prefix ``/api/v1/deliverables``):

* ``deliverables list`` → ``GET /api/v1/deliverables?project_id=&limit=``
  (cross-project tenant view when ``--project-id`` is omitted).
* ``deliverables show <id>`` → derived view: pages the list and filters
  client-side. The backend has no GET-by-id endpoint today; once one
  is added the lookup will switch to a single GET.
* ``deliverables attach <id> --path …`` → backend has no attach
  endpoint yet. The command surfaces a friendly one-line "not
  supported" message rather than silently constructing an incorrect
  request. Dry-run still renders ``supported=false`` so scripts can
  detect the gap.

``--dry-run`` skips HTTP for every command and renders the planned
request shape.
"""

from __future__ import annotations

from typing import Any

import typer

from backend.src.cli._client import build_http_client
from backend.src.cli.commands._common import emit_dry_run, emit_http_error, run_async

app = typer.Typer(
    name="deliverables",
    help="List founder Deliverables (per-project or tenant-wide).",
    no_args_is_help=True,
    add_completion=False,
)

_DEFAULT_LIMIT = 50
_LIST_PATH = "/api/v1/deliverables"


def _list_params(project_id: str | None, limit: int) -> dict[str, Any]:
    params: dict[str, Any] = {"limit": limit}
    if project_id is not None:
        params["project_id"] = project_id
    return params


@app.command("list", help="List Deliverables (filter with --project-id).")
def list_cmd(
    ctx: typer.Context,
    project_id: str | None = typer.Option(
        None,
        "--project-id",
        help="Filter to a single project. Omit for tenant-wide cross-project list.",
    ),
    limit: int = typer.Option(_DEFAULT_LIMIT, "--limit", min=1, max=200),
) -> None:
    obj = ctx.obj
    params = _list_params(project_id, limit)

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


@app.command("show", help="Show a single Deliverable by id (derived from list).")
def show_cmd(
    ctx: typer.Context,
    deliverable_id: str = typer.Argument(..., help="Deliverable UUID."),
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
        help="How many recent Deliverables to scan when filtering.",
    ),
) -> None:
    obj = ctx.obj
    params = _list_params(project_id, limit)

    if obj.dry_run:
        emit_dry_run(
            obj,
            {
                "method": "GET",
                "path": _LIST_PATH,
                "params": params,
                "filter_deliverable_id": deliverable_id,
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
        if str(row.get("id")) == deliverable_id:
            obj.formatter.emit(row)
            return

    typer.echo(
        f"Error: deliverable {deliverable_id} not found in latest {limit} entries.",
        err=True,
    )
    raise typer.Exit(code=1)


@app.command(
    "attach",
    help="Attach a file to a Deliverable (not supported by the backend yet).",
)
def attach_cmd(
    ctx: typer.Context,
    deliverable_id: str = typer.Argument(..., help="Deliverable UUID."),
    path: str = typer.Option(..., "--path", help="Local path of the artifact to attach."),
) -> None:
    obj = ctx.obj

    if obj.dry_run:
        emit_dry_run(
            obj,
            {
                "method": "POST",
                "path": f"/api/v1/deliverables/{deliverable_id}/attach",
                "body": {"path": path},
                "supported": False,
                "reason": "backend exposes no attach endpoint",
            },
        )
        return

    typer.echo(
        "Error: 'deliverables attach' is not supported — the backend exposes no attach endpoint.",
        err=True,
    )
    raise typer.Exit(code=1)


__all__ = ["app"]
