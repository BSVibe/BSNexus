"""Tests for the executor factory.

The principle (#39): every LLM call site must go through
`get_executor_for_agent`. Direct instantiation of LiteLLMExecutor /
ClaudeCodeExecutor is a bug — `executor_configs.executor_type` gets
silently ignored otherwise.

These tests cover the minimum viable factory that session 10 ships so
a claude_code cross-check is possible. A full unified interface is
#39's longer-term work.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio

from backend.src.models import Agent
from backend.src.models.executor_config import ExecutorConfig
from backend.src.models.tenant import Tenant


def _make_agent(tenant_id: uuid.UUID, executor_type: str = "generic_llm") -> Agent:
    return Agent(
        id=uuid.uuid4(), tenant_id=tenant_id, name="A", role="agent",
        executor_type=executor_type, executor_config={}, capabilities=["coding"],
        is_active=True,
    )


@pytest_asyncio.fixture
async def tenant_with_configs(test_session_maker):
    """Tenant seeded with two executor configs — one generic_llm (default), one claude_code."""
    tenant_id = uuid.uuid4()
    async with test_session_maker() as db:
        db.add(Tenant(id=tenant_id, name="T", slug="t", owner_user_id="u"))
        await db.flush()
        db.add(ExecutorConfig(
            id=uuid.uuid4(), tenant_id=tenant_id, name="default-llm",
            executor_type="generic_llm",
            config={"model": "ollama/glm-4.7-flash", "api_key": "unused",
                    "base_url": "http://localhost:11434"},
            is_default=True,
        ))
        db.add(ExecutorConfig(
            id=uuid.uuid4(), tenant_id=tenant_id, name="claude-cli",
            executor_type="claude_code",
            config={},
            is_default=False,
        ))
        await db.commit()
    return tenant_id


class TestGetExecutorForAgent:
    @pytest.mark.asyncio
    async def test_agent_without_executor_type_uses_tenant_default(
        self, tenant_with_configs, test_session_maker,
    ):
        from backend.src.core.executor.factory import get_executor_for_agent

        tenant_id = tenant_with_configs
        agent = _make_agent(tenant_id, executor_type="")
        async with test_session_maker() as db:
            exec_obj, model_cfg = await get_executor_for_agent(agent, db)

        # tenant default is generic_llm → LiteLLM
        from backend.src.core.executor.litellm_executor import LiteLLMExecutor
        assert isinstance(exec_obj, LiteLLMExecutor)
        assert model_cfg["model"] == "ollama/glm-4.7-flash"

    @pytest.mark.asyncio
    async def test_agent_with_claude_code_type_returns_adapter(
        self, tenant_with_configs, test_session_maker,
    ):
        """When agent.executor_type == 'claude_code', factory must return the
        ClaudeCode adapter (not LiteLLMExecutor). This is the failure mode
        agent_chat._call_via_worker's hardcode produced before #39."""
        from backend.src.core.executor.factory import get_executor_for_agent

        tenant_id = tenant_with_configs
        agent = _make_agent(tenant_id, executor_type="claude_code")
        async with test_session_maker() as db:
            exec_obj, model_cfg = await get_executor_for_agent(agent, db)

        from backend.src.core.executor.claude_code_adapter import (
            ClaudeCodeLLMAdapter,
        )
        assert isinstance(exec_obj, ClaudeCodeLLMAdapter)

    @pytest.mark.asyncio
    async def test_factory_falls_back_to_litellm_for_unknown_type(
        self, tenant_with_configs, test_session_maker,
    ):
        from backend.src.core.executor.factory import get_executor_for_agent

        tenant_id = tenant_with_configs
        agent = _make_agent(tenant_id, executor_type="unknown_type_xyz")
        async with test_session_maker() as db:
            exec_obj, model_cfg = await get_executor_for_agent(agent, db)

        from backend.src.core.executor.litellm_executor import LiteLLMExecutor
        assert isinstance(exec_obj, LiteLLMExecutor)


class TestClaudeCodeLLMAdapter:
    """The adapter lets ClaudeCodeExecutor plug into the (messages, tools,
    tool_handler, ...) interface LiteLLMExecutor exposes.

    Scope for session 10 is minimal: flatten the chat history into a
    single prompt, invoke claude CLI, wrap stdout in the LiteLLM-shaped
    ExecutionResult so downstream persistence / marker parsing keeps
    working unchanged.
    """

    @pytest.mark.asyncio
    async def test_execute_flattens_messages_and_returns_content(self, tmp_path):
        from backend.src.core.executor.base import ExecutionResult as TaskResult
        from backend.src.core.executor.claude_code_adapter import (
            ClaudeCodeLLMAdapter,
        )

        # Stub the underlying ClaudeCodeExecutor so we don't shell out.
        fake_cli = AsyncMock()
        fake_cli.execute = AsyncMock(return_value=TaskResult(
            success=True, stdout="final reply from claude",
        ))
        adapter = ClaudeCodeLLMAdapter(
            cli=fake_cli, workspace_dir=str(tmp_path),
        )

        result = await adapter.execute(
            messages=[
                {"role": "system", "content": "You are an agent."},
                {"role": "user", "content": "write code"},
            ],
            tools=None, tool_handler=None,
            model="claude_code", api_key="unused", base_url=None,
        )
        # Fields compatible with downstream LiteLLM ExecutionResult consumers.
        assert result.content == "final reply from claude"
        assert result.tool_calls_made == []
        assert result.stop_reason == "end_turn"
        # Verify flattening — both system and user text forwarded.
        called_prompt = fake_cli.execute.await_args.args[0]
        assert "You are an agent." in called_prompt
        assert "write code" in called_prompt

    @pytest.mark.asyncio
    async def test_execute_surfaces_cli_error_as_empty_content(self, tmp_path):
        from backend.src.core.executor.base import ExecutionResult as TaskResult
        from backend.src.core.executor.claude_code_adapter import (
            ClaudeCodeLLMAdapter,
        )

        fake_cli = AsyncMock()
        fake_cli.execute = AsyncMock(return_value=TaskResult(
            success=False, error_message="rate limited", stdout="",
        ))
        adapter = ClaudeCodeLLMAdapter(
            cli=fake_cli, workspace_dir=str(tmp_path),
        )
        result = await adapter.execute(
            messages=[{"role": "user", "content": "x"}],
            tools=None, tool_handler=None,
            model="claude_code", api_key="unused",
        )
        # Downstream expects content even on failure; error surfaces via
        # empty content + stop_reason.
        assert result.content == ""
        assert result.stop_reason == "error"


@pytest.mark.asyncio
async def test_agent_chat_dispatches_through_factory(monkeypatch):
    """Regression lock: the agent-chat LLM call path must dispatch
    through the factory so executor_configs.executor_type is honored.
    If anyone reintroduces LiteLLMExecutor() as a hardcoded call, this
    test trips.
    """
    import inspect

    from backend.src.api import agent_chat

    src = inspect.getsource(agent_chat._call_via_executor)
    assert "LiteLLMExecutor()" not in src, (
        "LiteLLMExecutor() must not be hardcoded — use "
        "get_executor_for_agent() so claude_code / future executors work."
    )
    assert "get_executor_for_agent" in src
