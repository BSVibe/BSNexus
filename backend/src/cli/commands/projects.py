"""``bsnexus projects`` sub-app — wraps the projects REST router.

Maps to ``backend.src.api.projects`` (prefix ``/api/v1/projects``):

* ``projects list`` → ``GET /api/v1/projects``
* ``projects show <id>`` → ``GET /api/v1/projects/{id}``
* ``projects create --name --description`` → ``POST /api/v1/projects``
* ``projects archive <id>`` → ``DELETE /api/v1/projects/{id}``

``--dry-run`` skips HTTP and renders the planned request shape.
"""

from __future__ import annotations

from typing import Any

import typer

from .._client import build_http_client
from ._common import emit_dry_run, emit_http_error, run_async

app = typer.Typer(
    name="projects",
    help="Manage BSNexus projects (tenant-scoped CRUD).",
    no_args_is_help=True,
    add_completion=False,
)


@app.command("list", help="List projects in the active tenant.")
def list_cmd(ctx: typer.Context) -> None:
    obj = ctx.obj
    path = "/api/v1/projects"

    if obj.dry_run:
        emit_dry_run(obj, {"method": "GET", "path": path})
        return

    async def _go() -> Any:
        client = build_http_client(obj)
        try:
            return await client.get(path)
        finally:
            await client.aclose()

    resp = run_async(_go)
    if resp.status_code >= 400:
        emit_http_error(resp)
        raise typer.Exit(code=1)

    obj.formatter.emit(resp.json())


@app.command("show", help="Show a single project by id.")
def show_cmd(
    ctx: typer.Context,
    project_id: str = typer.Argument(..., help="Project UUID."),
) -> None:
    obj = ctx.obj
    path = f"/api/v1/projects/{project_id}"

    if obj.dry_run:
        emit_dry_run(obj, {"method": "GET", "path": path})
        return

    async def _go() -> Any:
        client = build_http_client(obj)
        try:
            return await client.get(path)
        finally:
            await client.aclose()

    resp = run_async(_go)
    if resp.status_code >= 400:
        emit_http_error(resp)
        raise typer.Exit(code=1)

    obj.formatter.emit(resp.json())


@app.command("create", help="Create a new project.")
def create_cmd(
    ctx: typer.Context,
    name: str = typer.Option(..., "--name", help="Project name."),
    description: str = typer.Option("", "--description", help="Project description."),
    bsage_workspace_id: str | None = typer.Option(
        None,
        "--bsage-workspace-id",
        help="Optional BSage workspace id binding.",
    ),
    bsupervisor_policy_id: str | None = typer.Option(
        None,
        "--bsupervisor-policy-id",
        help="Optional BSupervisor policy id binding.",
    ),
) -> None:
    obj = ctx.obj
    body: dict[str, Any] = {"name": name, "description": description}
    if bsage_workspace_id is not None:
        body["bsage_workspace_id"] = bsage_workspace_id
    if bsupervisor_policy_id is not None:
        body["bsupervisor_policy_id"] = bsupervisor_policy_id

    path = "/api/v1/projects"
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


@app.command("archive", help="Archive (delete) a project.")
def archive_cmd(
    ctx: typer.Context,
    project_id: str = typer.Argument(..., help="Project UUID."),
) -> None:
    obj = ctx.obj
    path = f"/api/v1/projects/{project_id}"

    if obj.dry_run:
        emit_dry_run(obj, {"method": "DELETE", "path": path})
        return

    async def _go() -> Any:
        client = build_http_client(obj)
        try:
            return await client.delete(path)
        finally:
            await client.aclose()

    resp = run_async(_go)
    if resp.status_code >= 400:
        emit_http_error(resp)
        raise typer.Exit(code=1)

    obj.formatter.emit({"archived": project_id})


__all__ = ["app"]
