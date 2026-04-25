"""YAML prompt loader with variant selection + caching.

YAML schema (one file per logical prompt)::

    name: replanner
    version: 1
    description: Iterative replanner system prompt.
    variants:
      default:
        body: |
          Multi-line prompt text...
      experiment_a:
        body: |
          Alternative phrasing for A/B test...

Persona list YAML (a list of persona dicts under each variant)::

    name: worker-personas
    variants:
      default:
        personas:
          - name: builder
            system_prompt_template: ...
            tools: [...]
            keywords: [...]
            default_fit: 0.6

Variant selection: env var ``PROMPT_VARIANT_<NAME_UPPERCASED_WITH_UNDERSCORES>``.
Unknown variant logs a warning and falls back to ``default``.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Any

import structlog
import yaml

logger = structlog.get_logger(__name__)


class PromptNotFound(LookupError):
    """Raised when a prompt file or its ``default`` variant is missing."""


_DEFAULT_TEMPLATES_DIR = Path(__file__).parent / "templates"


def _normalize_env_key(name: str) -> str:
    return "PROMPT_VARIANT_" + name.upper().replace("-", "_")


class PromptRegistry:
    """Thread-safe in-memory cache over a directory of prompt YAML files."""

    def __init__(self, templates_dir: Path | None = None):
        self._dir = Path(templates_dir or _DEFAULT_TEMPLATES_DIR)
        self._cache: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()

    def _resolve_variant(self, name: str, available: dict[str, Any]) -> str:
        env_value = os.environ.get(_normalize_env_key(name))
        if env_value and env_value in available:
            return env_value
        if env_value and env_value not in available:
            logger.warning(
                "prompt_variant_not_found_falling_back",
                name=name,
                requested=env_value,
                available=sorted(available.keys()),
            )
        if "default" not in available:
            raise PromptNotFound(
                f"prompt {name!r} has no 'default' variant (available: {sorted(available)})"
            )
        return "default"

    def _load_file(self, name: str) -> dict[str, Any]:
        with self._lock:
            cached = self._cache.get(name)
            if cached is not None:
                return cached

            path = self._dir / f"{name}.yaml"
            if not path.exists():
                raise PromptNotFound(f"prompt file not found: {path}")

            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            variants = data.get("variants") or {}
            if not isinstance(variants, dict) or not variants:
                raise PromptNotFound(f"prompt {name!r} has no variants block")

            self._cache[name] = data
            return data

    def load(self, name: str) -> str:
        """Return the body string for ``name`` under the active variant."""
        data = self._load_file(name)
        variants = data["variants"]
        variant = self._resolve_variant(name, variants)
        body = variants[variant].get("body")
        if not isinstance(body, str):
            raise PromptNotFound(
                f"prompt {name!r} variant {variant!r} has no string ``body``"
            )
        return body

    def load_personas(self, name: str) -> list[dict[str, Any]]:
        """Return a persona list for ``name`` under the active variant."""
        data = self._load_file(name)
        variants = data["variants"]
        variant = self._resolve_variant(name, variants)
        personas = variants[variant].get("personas")
        if not isinstance(personas, list):
            raise PromptNotFound(
                f"prompt {name!r} variant {variant!r} has no ``personas`` list"
            )
        return personas

    def reset(self) -> None:
        with self._lock:
            self._cache.clear()


_GLOBAL_REGISTRY = PromptRegistry()


def load_prompt(name: str) -> str:
    return _GLOBAL_REGISTRY.load(name)


def load_persona_list(name: str) -> list[dict[str, Any]]:
    return _GLOBAL_REGISTRY.load_personas(name)


def reset_cache() -> None:
    _GLOBAL_REGISTRY.reset()
