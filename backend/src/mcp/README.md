# BSNexus MCP — first-class API surface

Phase 7 of the BSVibe AI-Native Control Plane (2026-05-08) treats MCP
as a first-class API alongside REST. Tools are not auto-derived from
the Typer CLI — every tool declares Pydantic input/output schemas, an
async handler, the bearer scopes it requires, and (for mutations) the
audit event it emits on success. Both the HTTP transport mounted at
`/mcp/admin/*` and the stdio transport launched by `bsnexus mcp serve`
share one in-process `ToolRegistry`.

## The `Tool` primitive

Defined in `api.py`. A tool is a value, not a decorator:

```python
from pydantic import BaseModel
from backend.src.mcp.api import Tool, ToolContext

class MyInput(BaseModel):
    project_id: str

class MyOutput(BaseModel):
    ok: bool

async def handler(args: MyInput, ctx: ToolContext) -> MyOutput:
    return MyOutput(ok=True)

MY_TOOL = Tool(
    name="bsnexus_things_do",
    description="Short, imperative — surfaces in ListTools.",
    input_schema=MyInput,
    output_schema=MyOutput,
    handler=handler,
    required_scopes=["bsnexus:things:write"],
    audit_event="nexus.thing.updated",  # None for read-only tools
)
```

The dispatcher (`ToolRegistry.call_tool`) runs the same pipeline for
every tool:

1. Validate `args` against `input_schema` (Pydantic) → raise
   `ToolValidationError` on mismatch.
2. Enforce `required_scopes` against `ctx.user.scope` (wildcard `*`
   counts) → raise `ToolPermissionError` on shortfall.
3. Run `handler(args, ctx)`. Any unexpected exception is wrapped in
   `ToolHandlerError` whose message is the class name only — raw
   exception text is never echoed (token-leak guard).
4. Validate the return value against `output_schema`.
5. If `audit_event` is set, emit a `bsvibe_audit.AuditEventBase` with
   `actor_from_user(ctx.user)`, `tenant_id=ctx.user.active_tenant_id`,
   `data={"tool": name}` — only on success.

The error hierarchy maps cleanly to MCP / HTTP error codes:
`ToolNotFoundError` → 404, `ToolPermissionError` → 403,
`ToolValidationError` → 422, `ToolHandlerError` → 500.

## How to add a tool

1. Define `input_schema` and `output_schema` as Pydantic models.
   Reuse existing schemas from `backend.src.schemas.*` whenever an
   admin tool mirrors a REST route — same wire shape, no parallel
   model.
2. Write the async handler. Call the **service-layer** function the
   REST router calls (e.g. `_persist_project_with_audit`), not the
   Typer command — CLI and MCP both delegate to the service so the
   audit / outbox path is the single source of truth.
3. Pick scopes:
   * Admin tools (called by operators / scripts via bearer auth):
     `bsnexus:<resource>:<action>` matching the REST route.
   * Domain tools (called by run workers via run-scoped HMAC):
     the sentinel `DOMAIN_RUN_SCOPE = "bsnexus:mcp:run"`.
4. Set `audit_event` on every mutation. Use the same event the REST
   router emits (e.g. `nexus.decision.locked`,
   `nexus.deliverable.created`) so REST + MCP funnel into one audit
   stream.
5. Register the tool in `register_admin_tools(registry)` (admin) or
   `register_domain_tools(registry)` (domain). Both are called once
   from `server.get_registry()`.
6. Write a test. The MCP test pattern (memory
   `mcp-python-sdk-testing`) is in-process: instantiate a
   `ToolRegistry`, register the tool, call `await registry.call_tool(
   name, args, ctx)` directly. Do **not** spawn subprocesses.

## Tool catalog

### Domain tools (`domain_tools.py`, run-worker auth)

| Name | Mutating | Audit event |
|------|----------|-------------|
| `decision_create` | yes | `nexus.decision.created` |
| `decision_wait` | no | — |
| `artifact_list` | no | — |
| `artifact_read` | no | — |
| `deliverable_report` | yes | `nexus.deliverable.created` |
| `knowledge_search` | no | — |

All require scope `bsnexus:mcp:run`, which the run-scoped HMAC token
grants for the lifetime of one execution run.

### Admin tools (`admin_tools.py`, bearer auth)

20 tools across 6 sub-apps, each named `bsnexus_<subapp>_<action>`:

* **projects** — `list`, `show`, `create`, `archive`
* **requests** — `list`, `show`, `create`, `update`
* **decisions** — `list`, `show`, `lock`, `unlock` *(unlock raises:
  no backing REST endpoint — surface kept for CLI parity)*
* **deliverables** — `list`, `show`, `attach` *(attach raises: no
  backing REST endpoint)*
* **events** — `list` *(raises: no backing REST endpoint)*
* **integrations** — `list`, `add`, `remove`, `test`

Every admin tool requires `bsnexus:<resource>:<action>`. Mutations
emit the same `nexus.*` audit event the REST router emits.

## Transports

### HTTP — `/mcp/admin/*` (lifespan-mounted)

Mounted by `backend.src.main` alongside REST. Two endpoints:

* `GET /mcp/admin/tools` →
  `{tools: [{name, description, input_schema, output_schema,
  required_scopes, audit_event}, ...]}`
* `POST /mcp/admin/tools/{name}` with the tool's JSON args as body →
  the tool's output JSON, or a 4xx/5xx mapped from the dispatcher
  error.

Both endpoints authenticate via `resolve_tool_context`, which mirrors
the REST 3-way dispatch in `core.auth`:

1. `Authorization: Bearer bv-bs-...` → `verify_bootstrap_token`.
2. `Authorization: Bearer bv-op-...` → `verify_opaque_token` via
   the cached introspection client.
3. Anything else → `verify_user_jwt`.

`X-Tenant-Id` is honored when the resolved `User` has no
`active_tenant_id` (bootstrap tokens carry scope `*` but no tenant).

The legacy `/mcp/health` and `/mcp/http` (FastMCP streamable-HTTP)
endpoints stay reserved for **run workers** — their auth model is the
per-run HMAC token, deliberately different from admin bearer auth.
`GET /mcp/health` (no token) is also a public liveness probe that
returns `{ok, tool_count}`; pass `?token=...` to additionally verify a
run-scoped HMAC and echo its claim.

### stdio — `bsnexus mcp serve --transport stdio`

Boots FastMCP's stdio loop against the same registry. Auth context for
stdio comes from the `BSV_BOOTSTRAP_TOKEN` env var, which
`resolve_tool_context` consumes the same way it consumes a
`Authorization` header. The token's presence is reported on
`--dry-run` but its **value is never echoed or logged**.

`bsnexus mcp list-tools` dumps the local registry catalog without any
HTTP round-trip — useful for confirming a deploy.

## Auth resolution at a glance

| Caller | Transport | Header / env | Tool family |
|--------|-----------|--------------|-------------|
| Run worker (claude/codex/opencode) | `/mcp/http` streamable-HTTP | `?token=<HMAC>` | Domain |
| Operator / script | `/mcp/admin/tools/*` | `Authorization: Bearer ...` | Admin |
| stdio integration | `bsnexus mcp serve --transport stdio` | `BSV_BOOTSTRAP_TOKEN` env | Admin (+ domain catalog visible) |

Tokens never appear in logs. `structlog` redaction is the default;
handler errors are wrapped to expose only the exception class name.

## Testing

* In-process pattern (memory `mcp-python-sdk-testing`): build a
  `ToolRegistry`, register tools, call `registry.call_tool(...)`
  directly. Do **not** spawn subprocesses.
* Help-text tests on the `bsnexus mcp` Typer sub-app must strip ANSI
  escapes before substring assertions (Phase 3 lesson):

  ```python
  import re
  out = re.sub(r"\x1b\[[0-9;]*m", "", result.output)
  assert "--transport" in out
  ```

* Coverage target: `uv run pytest --cov=src --cov-fail-under=80`.
  Current `src/mcp/*` coverage sits >=85%.
