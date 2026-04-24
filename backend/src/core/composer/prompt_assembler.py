"""PromptAssembler — pure function that composes a run's system prompt.

Owns the persona semantic: templates, tool lists, risk budgets. Takes
knowledge fragments from BSage (or empty list from NoopKnowledgeClient)
and produces a ``Composition`` ready to persist as a snapshot.

Deterministic given (template, fragments). Inject fake knowledge client
in tests for full control.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from backend.src.core.composer.knowledge_client import KnowledgeClient, KnowledgeFragment

if TYPE_CHECKING:
    from backend.src.models.execution_run import ExecutionRun


@dataclass(frozen=True)
class PersonaTemplate:
    """A named composition recipe. Matched against run intent + tools."""

    name: str
    system_prompt_template: str
    tools: list[str]
    keywords: list[str] = field(default_factory=list)
    default_fit: float = 0.5

    def score_for(self, intent: str, tools_available: list[str]) -> float:
        """Higher score = better fit. Simple keyword + tool overlap heuristic."""
        intent_lower = intent.lower()
        keyword_hits = sum(1 for k in self.keywords if k.lower() in intent_lower)
        tool_hits = len(set(self.tools) & set(tools_available or []))
        if not self.keywords and not self.tools:
            return self.default_fit
        keyword_score = keyword_hits / max(len(self.keywords), 1)
        tool_score = tool_hits / max(len(self.tools), 1)
        return round(0.6 * keyword_score + 0.4 * tool_score + self.default_fit * 0.1, 3)

    def render(self, fragments: list[KnowledgeFragment]) -> str:
        """Inline fragment excerpts into the template's placeholder."""
        if "{context}" not in self.system_prompt_template:
            return self.system_prompt_template
        if not fragments:
            context_block = "(no project-specific context available)"
        else:
            context_block = "\n\n".join(
                f"### {f.title}\n{f.excerpt}" for f in fragments
            )
        return self.system_prompt_template.replace("{context}", context_block)


@dataclass(frozen=True)
class Composition:
    """Output of ``PromptAssembler.compose``.

    Persisted as a ``CompositionSnapshot`` row; drives the executor.
    """

    source: str  # "bsage" | "local"
    system_prompt: str
    tools_allowed: list[str]
    context_doc_refs: list[dict]
    persona_label: str
    fit_score: float


class PersonaTemplateRegistry:
    """In-memory registry. Templates can be loaded from DB later."""

    def __init__(self, templates: list[PersonaTemplate] | None = None):
        self._templates: list[PersonaTemplate] = list(templates or [])

    def register(self, template: PersonaTemplate) -> None:
        self._templates.append(template)

    def pick(self, intent: str, tools_available: list[str] | None) -> PersonaTemplate:
        if not self._templates:
            raise LookupError("PersonaTemplateRegistry is empty")
        scored = [
            (t.score_for(intent, tools_available or []), t) for t in self._templates
        ]
        scored.sort(key=lambda x: x[0], reverse=True)
        return scored[0][1]

    def best_score(self, intent: str, tools_available: list[str] | None) -> float:
        if not self._templates:
            return 0.0
        return max(
            t.score_for(intent, tools_available or []) for t in self._templates
        )


class PromptAssembler:
    """Pure composer over a template registry + knowledge client."""

    def __init__(self, templates: PersonaTemplateRegistry):
        self._templates = templates

    async def compose(
        self,
        run: "ExecutionRun",
        knowledge: KnowledgeClient,
        *,
        intent_summary: str,
        tools_available: list[str] | None = None,
        top_k: int = 10,
    ) -> Composition:
        """Build a Composition for ``run`` using ``knowledge`` for context.

        ``intent_summary`` is typically ``run.request.intent_summary`` — passed
        explicitly so this function has no DB side effects and is fully
        testable.
        """
        fragments = await knowledge.search(intent_summary, top_k=top_k)
        template = self._templates.pick(intent_summary, tools_available)
        system_prompt = template.render(fragments)

        return Composition(
            source="bsage" if fragments else "local",
            system_prompt=system_prompt,
            tools_allowed=list(template.tools),
            context_doc_refs=[f.to_ref() for f in fragments],
            persona_label=template.name,
            fit_score=template.score_for(intent_summary, tools_available or []),
        )


# ─────────────────────────────────────────────────────────
# Default templates — minimal v1 set; extend via DB later.
# ─────────────────────────────────────────────────────────

_SHARED_POLICY = (
    "You work for the founder of an AI company. Execute the direction "
    "and deliver a complete, production-ready result — without asking "
    "for clarification. The founder is always short on time and expects "
    "you to make sensible defaults for anything they didn't spell out.\n\n"
    "File persistence:\n"
    "- When your work produces files (code, configs, docs, designs, data, "
    "anything), write them via the ``file_write`` tool. DO NOT paste file "
    "contents into the chat reply and expect them to be saved.\n"
    "- Use ``file_read`` to inspect earlier files in this project's "
    "workspace before overwriting them; ``file_list`` to see what's "
    "already there.\n"
    "- The chat reply is a short human summary of what shipped — never "
    "a substitute for writing files.\n\n"
    "Language: reply in the same natural language the founder used in "
    "their direction. Detect it from the user message; DO NOT translate. "
    "This applies to your chat reply, any prose/comments/docs inside "
    "files, titles, and error messages. Code identifiers stay in "
    "their conventional English form.\n\n"
    "Completeness: don't substitute a scaffold command (e.g. "
    "``npx create-next-app``, ``django-admin startproject``, ``cargo "
    "new``) for actual file contents. If a scaffold would generate "
    "files, write those files yourself via ``file_write``. Don't emit "
    "placeholder bodies (``TODO``, ``...``, empty functions) — write "
    "real working content."
)


_DEFAULT_TEMPLATES: list[PersonaTemplate] = [
    PersonaTemplate(
        name="builder",
        system_prompt_template=(
            "You are an engineer on the founder's team.\n\n"
            f"{_SHARED_POLICY}\n\n"
            "Relevant project context:\n{context}"
        ),
        tools=["file_read", "file_write", "file_list"],
        keywords=["implement", "fix", "build", "add", "refactor", "bug"],
        default_fit=0.6,
    ),
    PersonaTemplate(
        name="analyst",
        system_prompt_template=(
            "You are a research analyst on the founder's team.\n\n"
            f"{_SHARED_POLICY}\n\n"
            "Relevant project context:\n{context}"
        ),
        tools=["file_read", "file_write", "file_list"],
        keywords=["research", "analyze", "compare", "investigate", "summarize"],
        default_fit=0.5,
    ),
    PersonaTemplate(
        name="designer",
        system_prompt_template=(
            "You are a product designer on the founder's team.\n\n"
            f"{_SHARED_POLICY}\n\n"
            "Relevant project context:\n{context}"
        ),
        tools=["file_read", "file_write", "file_list"],
        keywords=["design", "ux", "ui", "mockup", "wireframe", "screen"],
        default_fit=0.5,
    ),
    PersonaTemplate(
        name="generalist",
        system_prompt_template=(
            "You are a Chief-of-Staff style generalist on the founder's "
            "team. Handle whatever the founder directs.\n\n"
            f"{_SHARED_POLICY}\n\n"
            "Relevant project context:\n{context}"
        ),
        tools=["file_read", "file_write", "file_list"],
        keywords=[],
        default_fit=0.4,
    ),
]


def default_template_registry() -> PersonaTemplateRegistry:
    return PersonaTemplateRegistry(_DEFAULT_TEMPLATES)
