from backend.src.core.executor.base import (
    BaseExecutor,
    ExecutionResult,
    ExecutorCapability,
    ExecutorInfo,
    ExecutorProtocol,
    ReviewResult,
)
from backend.src.core.executor.bsgateway import BSGatewayExecutor
from backend.src.core.executor.bsgateway import INFO as _bg_info
from backend.src.core.executor.claude_code import ClaudeCodeExecutor
from backend.src.core.executor.claude_code import INFO as _cc_info
from backend.src.core.executor.codex import CodexExecutor
from backend.src.core.executor.codex import INFO as _cx_info
from backend.src.core.executor.generic_llm import GenericLLMExecutor
from backend.src.core.executor.litellm_executor import LiteLLMExecutor
from backend.src.core.executor.registry import ExecutorRegistry

__all__ = [
    "BaseExecutor",
    "BSGatewayExecutor",
    "ClaudeCodeExecutor",
    "CodexExecutor",
    "ExecutionResult",
    "ExecutorCapability",
    "ExecutorInfo",
    "ExecutorProtocol",
    "ExecutorRegistry",
    "GenericLLMExecutor",
    "LiteLLMExecutor",
    "ReviewResult",
]

# Register built-in executors with capability info
_registry = ExecutorRegistry()

_GL_INFO = ExecutorInfo(
    name="generic_llm",
    capabilities=[
        ExecutorCapability.writing,
        ExecutorCapability.analysis,
        ExecutorCapability.marketing,
        ExecutorCapability.research,
        ExecutorCapability.general,
        ExecutorCapability.coding,
    ],
    requires_local=False,
    requires_workspace=True,
    description="Generic LLM with agentic tool_use loop via LiteLLM",
)


def _register_if_missing(name: str, factory: type, info: ExecutorInfo) -> None:
    if name not in _registry.list_available():
        _registry.register(name, factory, info=info)


_register_if_missing("claude_code", ClaudeCodeExecutor, _cc_info)
_register_if_missing("generic_llm", GenericLLMExecutor, _GL_INFO)
_register_if_missing("bsgateway", BSGatewayExecutor, _bg_info)
_register_if_missing("codex", CodexExecutor, _cx_info)


def create_executor(executor_type: str = "generic_llm") -> ExecutorProtocol:
    """Create an executor instance by type, resolved via ExecutorRegistry."""
    return _registry.get(executor_type)
