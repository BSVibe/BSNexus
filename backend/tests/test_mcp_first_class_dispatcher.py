"""First-class MCP dispatcher tests (TASK-002).

Covers the ``Tool`` / ``ToolContext`` / ``ToolRegistry`` primitive that
both domain (run-scoped) and admin (CLI-mirroring) MCP tools share.

Pattern matches ``mcp-python-sdk-testing`` memory: no subprocess —
exercise the registry directly. A few schema-level checks also use
``mcp.types.Tool`` to confirm what ``ListTools`` would expose over the
wire.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
import structlog
from bsvibe_authz import User
from pydantic import BaseModel, Field

from backend.src.mcp.api import (
    Tool,
    ToolContext,
    ToolHandlerError,
    ToolNotFoundError,
    ToolPermissionError,
    ToolRegistry,
    ToolValidationError,
    resolve_tool_context,
)


# ── shared dummy schemas ────────────────────────────────────────────


class _AddIn(BaseModel):
    a: int
    b: int


class _AddOut(BaseModel):
    sum: int


class _NameIn(BaseModel):
    name: str = Field(..., min_length=1)


class _NameOut(BaseModel):
    greeting: str


class _BoomIn(BaseModel):
    pass


class _BoomOut(BaseModel):
    ok: bool = True


# ── shared dummy handlers ───────────────────────────────────────────


async def _add_handler(payload: _AddIn, ctx: ToolContext) -> _AddOut:
    return _AddOut(sum=payload.a + payload.b)


async def _greet_handler(payload: _NameIn, ctx: ToolContext) -> _NameOut:
    return _NameOut(greeting=f"hi {payload.name}")


async def _boom_handler(payload: _BoomIn, ctx: ToolContext) -> _BoomOut:
    raise RuntimeError("internal kaboom — must not leak")


async def _bad_output_handler(payload: _AddIn, ctx: ToolContext) -> Any:
    # returns a dict that doesn't satisfy _AddOut
    return {"not_sum": "nope"}


# ── helpers ─────────────────────────────────────────────────────────


def _ctx(scope: list[str] | None = None) -> ToolContext:
    user = User(id="user-1", scope=scope or [])
    return ToolContext(
        settings=MagicMock(),
        user=user,
        db=None,
        audit_session=None,
        logger=structlog.get_logger("test.mcp"),
    )


def _mk_registry() -> ToolRegistry:
    reg = ToolRegistry()
    reg.register(
        Tool(
            name="add",
            description="add two ints",
            input_schema=_AddIn,
            output_schema=_AddOut,
            handler=_add_handler,
            required_scopes=[],
        )
    )
    reg.register(
        Tool(
            name="greet",
            description="say hi",
            input_schema=_NameIn,
            output_schema=_NameOut,
            handler=_greet_handler,
            required_scopes=["nexus:greet"],
        )
    )
    return reg


# ── registry surface ────────────────────────────────────────────────


def test_register_duplicate_raises() -> None:
    reg = _mk_registry()
    with pytest.raises(Exception):  # noqa: B017 — generic ToolError ok
        reg.register(
            Tool(
                name="add",
                description="dup",
                input_schema=_AddIn,
                output_schema=_AddOut,
                handler=_add_handler,
            )
        )


def test_list_tools_returns_mcp_types_with_schemas() -> None:
    reg = _mk_registry()
    tools = reg.list_tools()
    by_name = {t.name: t for t in tools}
    assert set(by_name) == {"add", "greet"}
    add_tool = by_name["add"]
    assert add_tool.description == "add two ints"
    schema = add_tool.inputSchema
    # schemas come straight from pydantic — keys are stable
    assert schema["type"] == "object"
    assert set(schema["properties"].keys()) == {"a", "b"}
    assert add_tool.outputSchema is not None
    assert add_tool.outputSchema["properties"]["sum"]["type"] == "integer"


# ── dispatch: schema validation ─────────────────────────────────────


async def test_call_tool_unknown_name_raises() -> None:
    reg = _mk_registry()
    with pytest.raises(ToolNotFoundError):
        await reg.call_tool("nope", {}, _ctx(scope=["*"]))


async def test_call_tool_invalid_input_raises_validation_error() -> None:
    reg = _mk_registry()
    with pytest.raises(ToolValidationError):
        # missing 'b' field
        await reg.call_tool("add", {"a": 1}, _ctx(scope=["*"]))


async def test_call_tool_returns_validated_output() -> None:
    reg = _mk_registry()
    out = await reg.call_tool("add", {"a": 2, "b": 3}, _ctx(scope=["*"]))
    assert isinstance(out, _AddOut)
    assert out.sum == 5


async def test_call_tool_output_schema_violation_raises() -> None:
    reg = ToolRegistry()
    reg.register(
        Tool(
            name="bad_out",
            description="returns wrong shape",
            input_schema=_AddIn,
            output_schema=_AddOut,
            handler=_bad_output_handler,
        )
    )
    with pytest.raises(ToolValidationError):
        await reg.call_tool("bad_out", {"a": 1, "b": 2}, _ctx(scope=["*"]))


# ── dispatch: scope enforcement ─────────────────────────────────────


async def test_scope_admin_wildcard_grants() -> None:
    reg = _mk_registry()
    out = await reg.call_tool("greet", {"name": "ada"}, _ctx(scope=["*"]))
    assert out.greeting == "hi ada"


async def test_scope_exact_match_grants() -> None:
    reg = _mk_registry()
    out = await reg.call_tool("greet", {"name": "ada"}, _ctx(scope=["nexus:greet"]))
    assert out.greeting == "hi ada"


async def test_scope_prefix_wildcard_grants() -> None:
    reg = _mk_registry()
    out = await reg.call_tool("greet", {"name": "ada"}, _ctx(scope=["nexus:*"]))
    assert out.greeting == "hi ada"


async def test_scope_missing_denied() -> None:
    reg = _mk_registry()
    with pytest.raises(ToolPermissionError):
        await reg.call_tool("greet", {"name": "ada"}, _ctx(scope=[]))


async def test_scope_unrelated_scope_denied() -> None:
    reg = _mk_registry()
    with pytest.raises(ToolPermissionError):
        await reg.call_tool("greet", {"name": "ada"}, _ctx(scope=["other:thing"]))


# ── dispatch: handler errors don't leak internals ──────────────────


async def test_handler_exception_wrapped_no_leak() -> None:
    reg = ToolRegistry()
    reg.register(
        Tool(
            name="boom",
            description="raises",
            input_schema=_BoomIn,
            output_schema=_BoomOut,
            handler=_boom_handler,
        )
    )
    with pytest.raises(ToolHandlerError) as excinfo:
        await reg.call_tool("boom", {}, _ctx(scope=["*"]))
    # never echoes the raw inner message — only the class name
    assert "kaboom" not in str(excinfo.value)


# ── audit emit ──────────────────────────────────────────────────────


async def test_audit_emit_fires_on_mutating_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[Any] = []

    async def fake_safe_emit(event: Any, *, session: Any) -> None:
        captured.append((event, session))

    monkeypatch.setattr("backend.src.mcp.api.safe_emit", fake_safe_emit)

    reg = ToolRegistry()
    reg.register(
        Tool(
            name="mutate",
            description="creates a thing",
            input_schema=_AddIn,
            output_schema=_AddOut,
            handler=_add_handler,
            audit_event="nexus.project.created",
        )
    )
    fake_session = MagicMock()
    ctx = ToolContext(
        settings=MagicMock(),
        user=User(id="user-1", scope=["*"], active_tenant_id="tenant-x"),
        db=None,
        audit_session=fake_session,
        logger=structlog.get_logger("test.mcp"),
    )
    await reg.call_tool("mutate", {"a": 1, "b": 2}, ctx)
    assert len(captured) == 1
    event, session = captured[0]
    assert session is fake_session
    assert event.event_type == "nexus.project.created"
    assert event.actor.id == "user-1"
    assert event.tenant_id == "tenant-x"
    # default data exposes the tool name (no input values, no token leakage)
    assert event.data.get("tool") == "mutate"
    assert "a" not in event.data
    assert "b" not in event.data


async def test_audit_emit_skipped_on_handler_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_emit = AsyncMock()
    monkeypatch.setattr("backend.src.mcp.api.safe_emit", fake_emit)

    reg = ToolRegistry()
    reg.register(
        Tool(
            name="mutate_boom",
            description="raises after audit_event set",
            input_schema=_BoomIn,
            output_schema=_BoomOut,
            handler=_boom_handler,
            audit_event="nexus.project.created",
        )
    )
    ctx = ToolContext(
        settings=MagicMock(),
        user=User(id="user-1", scope=["*"]),
        db=None,
        audit_session=MagicMock(),
        logger=structlog.get_logger("test.mcp"),
    )
    with pytest.raises(ToolHandlerError):
        await reg.call_tool("mutate_boom", {}, ctx)
    fake_emit.assert_not_called()


async def test_audit_emit_skipped_on_validation_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_emit = AsyncMock()
    monkeypatch.setattr("backend.src.mcp.api.safe_emit", fake_emit)

    reg = ToolRegistry()
    reg.register(
        Tool(
            name="mutate_validate",
            description="output validation fails",
            input_schema=_AddIn,
            output_schema=_AddOut,
            handler=_bad_output_handler,
            audit_event="nexus.project.created",
        )
    )
    ctx = ToolContext(
        settings=MagicMock(),
        user=User(id="user-1", scope=["*"]),
        db=None,
        audit_session=MagicMock(),
        logger=structlog.get_logger("test.mcp"),
    )
    with pytest.raises(ToolValidationError):
        await reg.call_tool("mutate_validate", {"a": 1, "b": 2}, ctx)
    fake_emit.assert_not_called()


async def test_audit_skipped_when_audit_event_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_emit = AsyncMock()
    monkeypatch.setattr("backend.src.mcp.api.safe_emit", fake_emit)

    reg = _mk_registry()  # 'add' has no audit_event
    await reg.call_tool("add", {"a": 1, "b": 2}, _ctx(scope=["*"]))
    fake_emit.assert_not_called()


async def test_audit_logged_warning_when_no_session(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_emit = AsyncMock()
    monkeypatch.setattr("backend.src.mcp.api.safe_emit", fake_emit)
    reg = ToolRegistry()
    reg.register(
        Tool(
            name="mutate_no_sess",
            description="no audit_session",
            input_schema=_AddIn,
            output_schema=_AddOut,
            handler=_add_handler,
            audit_event="nexus.project.created",
        )
    )
    out = await reg.call_tool(
        "mutate_no_sess",
        {"a": 1, "b": 2},
        ToolContext(
            settings=MagicMock(),
            user=User(id="user-1", scope=["*"]),
            db=None,
            audit_session=None,
            logger=structlog.get_logger("test.mcp"),
        ),
    )
    assert out.sum == 3
    fake_emit.assert_not_called()


# ── resolve_tool_context: 3-way auth dispatch ───────────────────────


async def test_resolve_tool_context_missing_bearer_raises() -> None:
    with pytest.raises(ToolPermissionError):
        await resolve_tool_context({}, settings=MagicMock())


async def test_resolve_tool_context_empty_bearer_raises() -> None:
    with pytest.raises(ToolPermissionError):
        await resolve_tool_context(
            {"authorization": "Bearer "},
            settings=MagicMock(),
        )


async def test_resolve_tool_context_bootstrap_token(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_user = User(id="bootstrap", is_service=True, scope=["*"])

    def fake_verify_bootstrap(token: str, az: Any) -> User:
        assert token == "bsv_admin_xyz"
        return fake_user

    monkeypatch.setattr("backend.src.mcp.api.verify_bootstrap_token", fake_verify_bootstrap)
    monkeypatch.setattr("backend.src.mcp.api._authz_settings", lambda: MagicMock())

    ctx = await resolve_tool_context(
        {"authorization": "Bearer bsv_admin_xyz"},
        settings=MagicMock(),
    )
    assert ctx.user is fake_user


async def test_resolve_tool_context_bad_token(monkeypatch: pytest.MonkeyPatch) -> None:
    from bsvibe_authz import AuthError

    def boom(*_a: Any, **_kw: Any) -> User:
        raise AuthError("invalid token")

    monkeypatch.setattr("backend.src.mcp.api.verify_bootstrap_token", boom)
    monkeypatch.setattr("backend.src.mcp.api._authz_settings", lambda: MagicMock())

    with pytest.raises(ToolPermissionError):
        await resolve_tool_context(
            {"authorization": "Bearer bsv_admin_bad"},
            settings=MagicMock(),
        )


async def test_resolve_tool_context_jwt_path(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_jwt(token: str, az: Any) -> dict[str, Any]:
        assert token == "ey.foo.bar"
        return {"sub": "u-7", "email": "u7@example.com"}

    monkeypatch.setattr("backend.src.mcp.api.verify_user_jwt", fake_jwt)
    monkeypatch.setattr("backend.src.mcp.api._authz_settings", lambda: MagicMock())

    ctx = await resolve_tool_context(
        {"authorization": "Bearer ey.foo.bar"},
        settings=MagicMock(),
    )
    assert ctx.user.id == "u-7"
    assert ctx.user.email == "u7@example.com"
