"""``bsnexus requests`` sub-app — wraps requests + conversation routers.

Maps to:

* ``requests list`` → ``GET /api/v1/requests?project_id=&limit=``
  (cross-project tenant view when ``--project-id`` is omitted).
* ``requests show <id>`` → derived view: pages the list and filters
  client-side. The backend does not currently expose
  ``GET /api/v1/requests/{id}``; once it does, this lookup will switch
  to a single GET. Surfacing ``show`` here gives operators the same
  command shape as the other resources.
* ``requests create --project-id --content`` → ``POST /api/v1/messages``
  with ``{project_id, content}``. The conversation router runs the
  inline request rule, which opens a new Request row when the content
  is non-empty.
* ``requests update --project-id --content`` → same endpoint, intended
  for follow-up messages on an in-flight request. The backend folds
  modification semantics into the next Request automatically.

``--dry-run`` skips HTTP and renders the planned request shape.
"""

from __future__ import annotations

from typing import Any

import typer

from backend.src.cli._client import build_http_client
from backend.src.cli.commands._common import emit_dry_run, emit_http_error, run_async

app = typer.Typer(
    name="requests",
    help="List + create founder Requests.",
    no_args_is_help=True,
    add_completion=False,
)

_DEFAULT_LIMIT = 50


def _list_path() -> str:
    return "/api/v1/requests"


def _list_params(project_id: str | None, limit: int) -> dict[str, Any]:
    params: dict[str, Any] = {"limit": limit}
    if project_id is not None:
        params["project_id"] = project_id
    return params


@app.command("list", help="List Requests for the active tenant (filter with --project-id).")
def list_cmd(
    ctx: typer.Context,
    project_id: str | None = typer.Option(
        None,
        "--project-id",
        help="Filter to a single project. Omit for cross-project tenant view.",
    ),
    limit: int = typer.Option(_DEFAULT_LIMIT, "--limit", min=1, max=200),
) -> None:
    obj = ctx.obj
    path = _list_path()
    params = _list_params(project_id, limit)

    if obj.dry_run:
        emit_dry_run(obj, {"method": "GET", "path": path, "params": params})
        return

    async def _go() -> Any:
        client = build_http_client(obj)
        try:
            return await client.get(path, params=params)
        finally:
            await client.aclose()

    resp = run_async(_go)
    if resp.status_code >= 400:
        emit_http_error(resp)
        raise typer.Exit(code=1)

    obj.formatter.emit(resp.json())


@app.command("show", help="Show a single Request by id (derived from list).")
def show_cmd(
    ctx: typer.Context,
    request_id: str = typer.Argument(..., help="Request UUID."),
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
        help="How many recent Requests to scan when filtering.",
    ),
) -> None:
    obj = ctx.obj
    path = _list_path()
    params = _list_params(project_id, limit)

    if obj.dry_run:
        emit_dry_run(
            obj,
            {
                "method": "GET",
                "path": path,
                "params": params,
                "filter_request_id": request_id,
            },
        )
        return

    async def _go() -> Any:
        client = build_http_client(obj)
        try:
            return await client.get(path, params=params)
        finally:
            await client.aclose()

    resp = run_async(_go)
    if resp.status_code >= 400:
        emit_http_error(resp)
        raise typer.Exit(code=1)

    rows = resp.json() or []
    for row in rows:
        if str(row.get("id")) == request_id:
            obj.formatter.emit(row)
            return

    typer.echo(f"Error: request {request_id} not found in latest {limit} entries.", err=True)
    raise typer.Exit(code=1)


def _send_message(obj: Any, project_id: str, content: str) -> None:
    body = {"project_id": project_id, "content": content}
    path = "/api/v1/messages"

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


@app.command("create", help="Open a new Request by sending a founder message.")
def create_cmd(
    ctx: typer.Context,
    project_id: str = typer.Option(..., "--project-id", help="Target project UUID."),
    content: str = typer.Option(..., "--content", help="Founder message body."),
) -> None:
    _send_message(ctx.obj, project_id, content)


@app.command("update", help="Append to the latest open Request via a follow-up message.")
def update_cmd(
    ctx: typer.Context,
    project_id: str = typer.Option(..., "--project-id", help="Target project UUID."),
    content: str = typer.Option(..., "--content", help="Follow-up message body."),
) -> None:
    _send_message(ctx.obj, project_id, content)


__all__ = ["app"]
