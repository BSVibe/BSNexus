"""Claude API executor — server-side code generation via LiteLLM tool_use.

No CLI subprocess needed. Suitable for SaaS where users don't have local Claude Code.
"""

from __future__ import annotations

import json
import structlog
from typing import Any

from litellm import acompletion

from backend.src.config import settings
from backend.src.core.executor.base import (
    ExecutionResult,
    ExecutorCapability,
    ExecutorInfo,
    ReviewResult,
)
from backend.src.prompts.loader import get_prompt

logger = structlog.get_logger(__name__)

INFO = ExecutorInfo(
    name="claude_api",
    capabilities=[ExecutorCapability.coding, ExecutorCapability.general],
    requires_local=False,
    requires_workspace=True,
    description="Claude API with tool_use for server-side code generation via LiteLLM",
)


class ClaudeAPIExecutor:
    """Execute tasks via LiteLLM acompletion with tool_use messages."""

    def __init__(self) -> None:
        self._model = settings.default_llm_model

    def supported_task_types(self) -> list[str]:
        return ["coding", "refactor", "bugfix", "test", "writing", "analysis"]

    async def execute(self, prompt: str, context: dict[str, Any]) -> ExecutionResult:
        model = context.get("model", self._model)
        try:
            response = await acompletion(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=context.get("max_tokens", 16384),
                temperature=context.get("temperature", 0.0),
            )
            content = response.choices[0].message.content or ""
            return ExecutionResult(success=True, stdout=content)
        except Exception as e:
            logger.error("claude_api_executor_error", error=str(e))
            return ExecutionResult(
                success=False,
                error_message=str(e),
                error_category="environment",
            )

    async def review(self, prompt: str, context: dict[str, Any]) -> ReviewResult:
        review_prompt = get_prompt("review", "code_review").format(task_prompt=prompt)
        result = await self.execute(review_prompt, context)
        if not result.success:
            return ReviewResult(
                passed=False,
                error_message=result.error_message,
                error_category=result.error_category,
            )
        passed = _parse_verdict(result.stdout)
        return ReviewResult(passed=passed, feedback=result.stdout)


def _parse_verdict(output: str) -> bool:
    """Parse PASS/FAIL verdict from review output."""
    for line in reversed(output.strip().splitlines()):
        cleaned = line.strip().upper()
        if "VERDICT: PASS" in cleaned or "RESULT: PASS" in cleaned:
            return True
        if "VERDICT: FAIL" in cleaned or "RESULT: FAIL" in cleaned:
            return False
    return False
