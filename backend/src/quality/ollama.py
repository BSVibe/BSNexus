from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib import request

from backend.src.core.domain import ProofState
from backend.src.quality.m0 import BenchmarkTask, TaskTelemetry


Transport = Callable[[str, dict[str, Any], float], dict[str, Any]]


@dataclass(frozen=True)
class LiveOllamaExecutor:
    """Live local-LLM probe for M0 harness runs.

    This intentionally does not let the model own proof state. Until the
    greenfield execution worker exists, the live probe records model response
    telemetry and leaves proof as missing.
    """

    model: str = "qwen3-coder:30b"
    endpoint: str = "http://host.docker.internal:11434/api/generate"
    timeout_s: float = 120
    transport: Transport | None = None

    async def __call__(self, task: BenchmarkTask) -> TaskTelemetry:
        payload = {
            "model": self.model,
            "prompt": _prompt_for(task),
            "stream": False,
            "options": {"temperature": 0.1},
        }
        transport = self.transport or _post_json
        response = await asyncio.to_thread(transport, self.endpoint, payload, self.timeout_s)
        response_text = str(response.get("response", ""))
        return TaskTelemetry(
            model=f"ollama_chat/{self.model}",
            scenario_id=task.id,
            total_rounds=1,
            phase_rounds={"prepare": 1, "work": 0, "verify": 0, "summarize": 0},
            tool_count=0,
            repeated_tool_sequence_count=0,
            deliverables_created=1 if response_text.strip() else 0,
            proof_state=ProofState.verification_missing,
            verifier_command=None,
            verifier_exit_code=None,
            decisions_created=0,
            terminal_reason="live_llm_sampled_without_execution_worker",
        )


def _post_json(endpoint: str, payload: dict[str, Any], timeout_s: float) -> dict[str, Any]:
    body = json.dumps(payload).encode("utf-8")
    req = request.Request(
        endpoint,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with request.urlopen(req, timeout=timeout_s) as response:
        return json.loads(response.read().decode("utf-8"))


def _prompt_for(task: BenchmarkTask) -> str:
    return "\n".join(
        [
            "You are participating in the BSNexus M0 local-LLM quality harness.",
            "Produce a concise implementation plan and expected proof command.",
            "Do not claim verified, shipped, or done.",
            f"Task id: {task.id}",
            f"Scenario: {task.scenario.value}",
            f"Title: {task.title}",
            f"Prompt: {task.prompt}",
            f"Expected proof: {task.expected_proof}",
        ]
    )
