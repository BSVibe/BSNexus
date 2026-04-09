"""Generic LLM executor — prompt-in, text-out for non-coding tasks.

Used for writing, analysis, marketing, research, and other text-generation tasks
that don't require tool_use or workspace access.
"""

from __future__ import annotations

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

logger = structlog.get_logger(__name__)

INFO = ExecutorInfo(
    name="generic_llm",
    capabilities=[
        ExecutorCapability.writing,
        ExecutorCapability.analysis,
        ExecutorCapability.marketing,
        ExecutorCapability.research,
        ExecutorCapability.general,
    ],
    requires_local=False,
    requires_workspace=False,
    description="Generic LLM for non-coding text generation tasks",
)


class GenericLLMExecutor:
    """Simple prompt → text executor for non-coding tasks."""

    def __init__(self) -> None:
        self._model = settings.default_llm_model

    def supported_task_types(self) -> list[str]:
        return ["writing", "analysis", "marketing", "research"]

    async def execute(self, prompt: str, context: dict[str, Any]) -> ExecutionResult:
        model = context.get("model", self._model)
        try:
            response = await acompletion(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=context.get("max_tokens", 16384),
                temperature=context.get("temperature", 0.7),
            )
            content = response.choices[0].message.content or ""
            return ExecutionResult(success=True, stdout=content)
        except Exception as e:
            logger.error("generic_llm_executor_error", error=str(e))
            return ExecutionResult(
                success=False,
                error_message=str(e),
                error_category="environment",
            )

    async def review(self, prompt: str, context: dict[str, Any]) -> ReviewResult:
        review_prompt = (
            "Review the following content for quality, accuracy, and completeness. "
            "End with VERDICT: PASS or VERDICT: FAIL - <reason>.\n\n"
            f"Content to review:\n{prompt}"
        )
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
    for line in reversed(output.strip().splitlines()):
        cleaned = line.strip().upper()
        if "VERDICT: PASS" in cleaned or "RESULT: PASS" in cleaned:
            return True
        if "VERDICT: FAIL" in cleaned or "RESULT: FAIL" in cleaned:
            return False
    return False
