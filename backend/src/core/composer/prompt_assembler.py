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
            context_block = "\n\n".join(f"### {f.title}\n{f.excerpt}" for f in fragments)
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
        scored = [(t.score_for(intent, tools_available or []), t) for t in self._templates]
        scored.sort(key=lambda x: x[0], reverse=True)
        return scored[0][1]

    def best_score(self, intent: str, tools_available: list[str] | None) -> float:
        if not self._templates:
            return 0.0
        return max(t.score_for(intent, tools_available or []) for t in self._templates)


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
        workspace_state: list[dict] | None = None,
        prior_iterations: list[dict] | None = None,
    ) -> Composition:
        """Build a Composition for ``run`` using ``knowledge`` for context.

        ``intent_summary`` is typically ``run.request.intent_summary`` — passed
        explicitly so this function has no DB side effects and is fully
        testable.

        ``workspace_state`` is the current list of files on disk (each
        ``{path, size}``). When provided it gets baked into the system
        prompt so weak LLMs don't have to remember to ``file_read`` the
        workspace.md context file before acting.

        ``prior_iterations`` is a list of completed iteration summaries
        (each ``{name, founder_summary, files_written}``) so the worker
        sees what's already been shipped and skips re-doing it.
        """
        fragments = await knowledge.search(intent_summary, top_k=top_k)
        template = self._templates.pick(intent_summary, tools_available)
        rendered = template.render(fragments)
        system_prompt = _bake_in_state(rendered, workspace_state, prior_iterations)

        return Composition(
            source="bsage" if fragments else "local",
            system_prompt=system_prompt,
            tools_allowed=list(template.tools),
            context_doc_refs=[f.to_ref() for f in fragments],
            persona_label=template.name,
            fit_score=template.score_for(intent_summary, tools_available or []),
        )


def _bake_in_state(
    base_prompt: str,
    workspace_state: list[dict] | None,
    prior_iterations: list[dict] | None,
) -> str:
    """Append a ``CURRENT WORKSPACE`` + ``PRIOR ITERATIONS`` block.

    Inlining this state means the worker doesn't have to issue 3
    ``file_read`` calls (stack.md / workspace.md / history.md) before
    its first real action — every saved tool call is one less chance to
    burn the iteration budget on bookkeeping. Weak LLMs that previously
    skipped the file_read step entirely now get the same information
    impossible to miss.
    """
    parts: list[str] = [base_prompt]

    if workspace_state is not None:
        parts.append("\n\n## CURRENT WORKSPACE (truth on disk)\n")
        if not workspace_state:
            parts.append("(empty — this is the first iteration. Pick a stack and create files.)")
        else:
            parts.append("Files already present. **Do NOT recreate any of these unless extending them.**\n")
            parts.append("```\n")
            for entry in workspace_state[:200]:
                path = str(entry.get("path") or "")
                size = entry.get("size") or 0
                parts.append(f"{path}\t{size} bytes\n")
            if len(workspace_state) > 200:
                parts.append(f"... ({len(workspace_state) - 200} more files)\n")
            parts.append("```\n")
            parts.append(
                "If your directive would create a file that already exists "
                "above, ``file_read`` it FIRST and EXTEND it. Identical "
                "rewrites are rejected by file_write."
            )

    if prior_iterations:
        parts.append("\n\n## PRIOR ITERATIONS (what's already been shipped)\n")
        for i, iteration in enumerate(prior_iterations, 1):
            name = str(iteration.get("name") or f"iteration {i}")
            summary = str(iteration.get("founder_summary") or iteration.get("summary") or "")[:400]
            files = iteration.get("files_written") or []
            parts.append(f"\n### Iteration {i}: {name}\n")
            if summary:
                parts.append(f"{summary}\n")
            if files:
                file_list = ", ".join(str(f)[:80] for f in files[:10])
                if len(files) > 10:
                    file_list += f", … ({len(files) - 10} more)"
                parts.append(f"\nFiles produced: {file_list}\n")
        parts.append(
            "\n**Your directive is the NEXT step after these iterations.** "
            "Do NOT reproduce work that's already in the file list above."
        )

    return "".join(parts)


# ─────────────────────────────────────────────────────────
# Default templates — minimal v1 set; extend via DB later.
# ─────────────────────────────────────────────────────────

def _build_default_templates() -> list[PersonaTemplate]:
    """Hydrate persona templates from YAML at call time.

    Persona shells live in ``prompts/templates/worker-personas.yaml``;
    each one contains a ``{shared_policy}`` placeholder we substitute
    with the body of ``worker-shared-policy.yaml`` here so the persona
    file stays focused on what's persona-specific.
    """
    from backend.src.core.prompts import load_persona_list, load_prompt

    shared_policy = load_prompt("worker-shared-policy")
    raw = load_persona_list("worker-personas")
    out: list[PersonaTemplate] = []
    for entry in raw:
        template = entry["system_prompt_template"].replace("{shared_policy}", shared_policy)
        out.append(
            PersonaTemplate(
                name=entry["name"],
                system_prompt_template=template,
                tools=list(entry.get("tools") or []),
                keywords=list(entry.get("keywords") or []),
                default_fit=float(entry.get("default_fit", 0.5)),
            )
        )
    return out


def default_template_registry() -> PersonaTemplateRegistry:
    return PersonaTemplateRegistry(_build_default_templates())
