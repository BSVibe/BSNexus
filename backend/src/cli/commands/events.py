"""``bsnexus events`` sub-app — tails the project SSE stream.

Maps to ``backend.src.api.project_events`` (prefix ``/api/v1/events``).
The backend exposes a single SSE endpoint — there is no REST list mode.
``bsnexus events list`` therefore connects to the SSE stream, accumulates
events until ``--limit`` is reached or ``--timeout`` elapses, then closes
the connection and emits the events as a JSON array via the active
:class:`~bsvibe_cli_base.OutputFormatter`.

* ``--project-id`` is required (the backend rejects an unscoped stream).
* ``--type X`` filters event types (e.g. ``run_transition``); omit to
  collect every event type emitted.
* ``--limit`` caps the number of events returned (default 10).
* ``--timeout`` caps the wait in seconds (default 5).
* ``--since`` is forwarded as a query param so future server-side
  filtering can use it; today the SSE endpoint ignores it.

``--dry-run`` skips the network call and renders the planned request
shape (path + params + filters).
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import typer

from .._client import build_http_client
from ._common import emit_dry_run, emit_http_error, run_async

app = typer.Typer(
    name="events",
    help="Tail the project SSE event stream as a bounded JSON array.",
    no_args_is_help=True,
    add_completion=False,
)

_LIST_PATH = "/events"
_DEFAULT_LIMIT = 10
_DEFAULT_TIMEOUT_S = 5.0


def _request_params(project_id: str, since: str | None) -> dict[str, Any]:
    params: dict[str, Any] = {"project_id": project_id}
    if since is not None:
        params["since"] = since
    return params


def _stream_headers(obj: Any) -> dict[str, str]:
    """Compose Authorization + X-Tenant-Id for the SSE stream call.

    ``CliHttpClient`` only merges its stored headers inside ``request()``;
    a direct call against ``client.http.stream(...)`` bypasses that path,
    so the SSE request would otherwise reach the backend without auth and
    422/401 against ``get_current_user`` + ``get_tenant_id``.
    """
    headers: dict[str, str] = {}
    token = getattr(obj, "token", None)
    if token:
        headers["Authorization"] = f"Bearer {token}"
    tenant_id = getattr(obj, "tenant_id", None)
    if tenant_id:
        headers["X-Tenant-Id"] = tenant_id
    return headers


async def _tail(
    client: Any,
    project_id: str,
    since: str | None,
    type_filter: str | None,
    limit: int,
    timeout_s: float,
    headers: dict[str, str] | None = None,
) -> tuple[Any, list[dict[str, Any]]]:
    events: list[dict[str, Any]] = []
    params = _request_params(project_id, since)

    resp_holder: dict[str, Any] = {}

    async def _read() -> None:
        async with client.http.stream("GET", _LIST_PATH, params=params, headers=headers) as resp:
            resp_holder["resp"] = resp
            if resp.status_code >= 400:
                return
            current: dict[str, str] = {}
            async for line in resp.aiter_lines():
                if not line:
                    if "data" in current:
                        raw = current["data"]
                        try:
                            data: Any = json.loads(raw)
                        except (ValueError, TypeError):
                            data = raw
                        evt_type = current.get("event") or "message"
                        if type_filter is None or evt_type == type_filter:
                            events.append({"event": evt_type, "data": data})
                            if len(events) >= limit:
                                return
                    current = {}
                    continue
                if line.startswith("event:"):
                    current["event"] = line[len("event:") :].strip()
                elif line.startswith("data:"):
                    current["data"] = line[len("data:") :].strip()

    try:
        await asyncio.wait_for(_read(), timeout=timeout_s)
    except asyncio.TimeoutError:
        pass

    return resp_holder.get("resp"), events


@app.command("list", help="Tail the project SSE stream until --limit or --timeout.")
def list_cmd(
    ctx: typer.Context,
    project_id: str = typer.Option(..., "--project-id", help="Project UUID to stream."),
    type_filter: str | None = typer.Option(
        None,
        "--type",
        help="Only keep events of this type (e.g. run_transition, deliverable, decision).",
    ),
    since: str | None = typer.Option(
        None,
        "--since",
        help="Forwarded to the server as a ?since= filter (server may ignore today).",
    ),
    limit: int = typer.Option(_DEFAULT_LIMIT, "--limit", min=1, max=500),
    timeout_s: float = typer.Option(
        _DEFAULT_TIMEOUT_S,
        "--timeout",
        min=0.1,
        max=300.0,
        help="Stop after this many seconds even if --limit was not reached.",
    ),
) -> None:
    obj = ctx.obj
    params = _request_params(project_id, since)
    filters = {"type": type_filter, "since": since, "limit": limit, "timeout_s": timeout_s}

    if obj.dry_run:
        emit_dry_run(
            obj,
            {
                "method": "GET",
                "path": _LIST_PATH,
                "params": params,
                "filters": filters,
            },
        )
        return

    headers = _stream_headers(obj)

    async def _go() -> tuple[Any, list[dict[str, Any]]]:
        client = build_http_client(obj)
        try:
            return await _tail(
                client,
                project_id,
                since,
                type_filter,
                limit,
                timeout_s,
                headers=headers or None,
            )
        finally:
            await client.aclose()

    resp, events = run_async(_go)
    if resp is not None and resp.status_code >= 400:
        emit_http_error(resp)
        raise typer.Exit(code=1)

    obj.formatter.emit(events)


__all__ = ["app"]
