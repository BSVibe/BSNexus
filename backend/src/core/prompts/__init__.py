"""YAML-backed prompt registry.

Externalizes hardcoded LLM prompts so they can be iterated on without
code edits, A/B tested via environment-variable variant selection, and
eventually moved to a DB without changing callsite code.

Usage::

    from backend.src.core.prompts import load_prompt, load_persona_list

    system = load_prompt("replanner")
    personas = load_persona_list("worker-personas")

A/B testing: set ``PROMPT_VARIANT_<NAME>`` (e.g.
``PROMPT_VARIANT_REPLANNER=experiment_a``). Unknown variants log a
warning and fall back to ``default``.
"""

from backend.src.core.prompts.registry import (
    PromptNotFound,
    PromptRegistry,
    load_persona_list,
    load_prompt,
    reset_cache,
)

__all__ = [
    "PromptNotFound",
    "PromptRegistry",
    "load_persona_list",
    "load_prompt",
    "reset_cache",
]
