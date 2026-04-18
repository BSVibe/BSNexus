"""Design tools — create and modify .bsd screen files in the workspace.

Screens are stored as JSON files under ``design/screens/<slug>.bsd``.
These tools replace the need for agents to use the REST API for design
operations — they write directly to the workspace filesystem.

The ``spec`` payload is validated against
:mod:`backend.src.core.bsd_schema` before being written so every screen
follows the same canonical structure (Pencil-Dev-style vocabulary).
"""

from __future__ import annotations

import json
import re
from typing import Any

import structlog

from backend.src.core.bsd_schema import (
    ALLOWED_TYPES,
    normalise_spec,
    validate_spec,
)
from backend.src.tools.base import Tool, ToolContext, ToolExecutionError

logger = structlog.get_logger(__name__)

DESIGN_DIR = "design"
SCREEN_DIR = f"{DESIGN_DIR}/screens"
SCREEN_EXT = ".bsd"

_SLUG_RE = re.compile(r"[^a-z0-9-]+")


def _slugify(value: str) -> str:
    slug = _SLUG_RE.sub("-", value.lower()).strip("-")
    return slug or "screen"


def _format_validation_errors(errors: list, limit: int = 6) -> str:
    """Build a short, actionable message listing the spec errors."""
    head = errors[:limit]
    lines = [f"- {e.format()}" for e in head]
    more = len(errors) - len(head)
    if more > 0:
        lines.append(f"- ... (+{more} more)")
    return (
        "Spec rejected — the .bsd file must follow the canonical schema. "
        "Fix the following and retry:\n" + "\n".join(lines) +
        f"\n\nAllowed types: {', '.join(sorted(ALLOWED_TYPES))}."
    )


class CreateScreenTool(Tool):
    """Create a new .bsd screen design specification."""

    @property
    def name(self) -> str:
        return "create_screen"

    @property
    def description(self) -> str:
        return (
            "Create a new screen design specification (.bsd file). "
            "Includes name, route, intent, and a spec object describing the UI structure."
        )

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Screen name (e.g., 'Login Page')"},
                "route": {"type": "string", "description": "URL route (e.g., '/login')"},
                "intent": {"type": "string", "description": "What this screen is for"},
                "spec": {
                    "type": "object",
                    "description": "UI structure spec (component tree)",
                },
                "generated_code": {
                    "type": "string",
                    "description": "Optional generated code preview",
                },
            },
            "required": ["name"],
        }

    async def execute(self, input: dict[str, Any], ctx: ToolContext) -> str:
        name = input["name"]
        raw_spec = input.get("spec") or {}

        # Normalise common LLM shortcuts (Container→View, flat props→props, …)
        # then validate against the canonical schema. Reject on any error so
        # the Designer agent gets an actionable message to fix the spec.
        spec = normalise_spec(raw_spec)
        errors = validate_spec(spec, strict=True)
        if errors:
            logger.info(
                "screen_spec_rejected",
                slug=_slugify(name),
                agent=ctx.agent_name,
                error_count=len(errors),
            )
            raise ToolExecutionError(_format_validation_errors(errors))

        slug = _slugify(name)
        screen_dir = ctx.workspace_path / SCREEN_DIR
        screen_dir.mkdir(parents=True, exist_ok=True)

        # Reject if screen with same slug already exists
        path = screen_dir / f"{slug}{SCREEN_EXT}"
        if path.exists():
            raise ToolExecutionError(
                f"Screen '{slug}' already exists. "
                f"Use modify_screen(slug=\"{slug}\") to update it instead of creating a duplicate."
            )

        # Fuzzy check against existing screens
        from difflib import SequenceMatcher

        for existing_path in screen_dir.glob(f"*{SCREEN_EXT}"):
            existing_slug = existing_path.stem
            if SequenceMatcher(None, slug, existing_slug).ratio() > 0.7:
                raise ToolExecutionError(
                    f"A similar screen '{existing_slug}' already exists. "
                    f"Use modify_screen(slug=\"{existing_slug}\") to update it, "
                    f"or choose a clearly different name."
                )

        data = {
            "name": name,
            "route": input.get("route"),
            "intent": input.get("intent"),
            "spec": spec,
            "generated_code": input.get("generated_code"),
        }
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False))

        logger.info("screen_created_via_tool", slug=slug, agent=ctx.agent_name)
        return json.dumps({
            "slug": slug,
            "path": f"{SCREEN_DIR}/{slug}{SCREEN_EXT}",
            "status": "created",
        })


class ModifyScreenTool(Tool):
    """Modify an existing .bsd screen design specification."""

    @property
    def name(self) -> str:
        return "modify_screen"

    @property
    def description(self) -> str:
        return "Update an existing screen's spec, route, intent, or generated code."

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "slug": {"type": "string", "description": "Screen slug (filename without .bsd)"},
                "name": {"type": "string", "description": "Updated screen name"},
                "route": {"type": "string", "description": "Updated route"},
                "intent": {"type": "string", "description": "Updated intent"},
                "spec": {"type": "object", "description": "Updated UI spec"},
                "generated_code": {"type": "string", "description": "Updated generated code"},
            },
            "required": ["slug"],
        }

    async def execute(self, input: dict[str, Any], ctx: ToolContext) -> str:
        slug = input["slug"]
        path = ctx.workspace_path / SCREEN_DIR / f"{slug}{SCREEN_EXT}"

        if not path.is_file():
            raise ToolExecutionError(f"Screen not found: {slug}")

        try:
            data = json.loads(path.read_text())
        except json.JSONDecodeError as e:
            raise ToolExecutionError(f"Corrupt .bsd file: {e}")

        # Update only provided fields; validate spec if it's being changed
        for field in ("name", "route", "intent", "spec", "generated_code"):
            if field in input:
                if field == "spec":
                    normalised = normalise_spec(input[field] or {})
                    errors = validate_spec(normalised, strict=True)
                    if errors:
                        logger.info(
                            "screen_spec_rejected",
                            slug=slug,
                            agent=ctx.agent_name,
                            error_count=len(errors),
                        )
                        raise ToolExecutionError(_format_validation_errors(errors))
                    data[field] = normalised
                else:
                    data[field] = input[field]

        path.write_text(json.dumps(data, indent=2, ensure_ascii=False))

        logger.info("screen_modified_via_tool", slug=slug, agent=ctx.agent_name)
        return json.dumps({"slug": slug, "status": "updated"})
