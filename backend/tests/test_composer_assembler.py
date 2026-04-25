"""Tests for PromptAssembler + NoopKnowledgeClient degradable path."""

from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

import pytest

from backend.src.core.composer import (
    KnowledgeFragment,
    NoopKnowledgeClient,
    PersonaTemplate,
    PersonaTemplateRegistry,
    PromptAssembler,
    default_template_registry,
)
from backend.src.core.composer.knowledge_client import resolve_knowledge_client
from backend.src.core.integrations.config import ProviderConfig


@dataclass
class FakeKnowledgeClient:
    """Test double that returns a scripted fragment list."""

    fragments: list[KnowledgeFragment]

    async def search(self, intent: str, *, top_k: int = 10) -> list[KnowledgeFragment]:
        return self.fragments[:top_k]

    async def fetch(self, path: str) -> str | None:
        return None

    async def backlinks(self, path: str) -> list[str]:
        return []


def _fake_run() -> Any:
    return SimpleNamespace(
        id="00000000-0000-0000-0000-000000000001",
        tenant_id="00000000-0000-0000-0000-000000000002",
    )


@pytest.mark.asyncio
async def test_noop_knowledge_client_returns_empty_fragments():
    client = NoopKnowledgeClient()
    assert await client.search("anything") == []
    assert await client.fetch("/some/path") is None
    assert await client.backlinks("/some/path") == []


@pytest.mark.asyncio
async def test_resolve_knowledge_client_returns_noop_when_disabled():
    client = resolve_knowledge_client(None)
    assert isinstance(client, NoopKnowledgeClient)

    disabled_cfg = ProviderConfig(enabled=False, base_url="http://bsage", api_key="k")
    client = resolve_knowledge_client(disabled_cfg)
    assert isinstance(client, NoopKnowledgeClient)


@pytest.mark.asyncio
async def test_resolve_knowledge_client_returns_bsage_when_configured():
    from backend.src.core.composer.knowledge_client import BSageKnowledgeClient

    cfg = ProviderConfig(enabled=True, base_url="http://bsage", api_key="k")
    client = resolve_knowledge_client(cfg)
    assert isinstance(client, BSageKnowledgeClient)


@pytest.mark.asyncio
async def test_assembler_marks_source_local_when_no_fragments():
    assembler = PromptAssembler(default_template_registry())
    composition = await assembler.compose(
        _fake_run(),
        NoopKnowledgeClient(),
        intent_summary="Implement a user profile page",
        tools_available=["read", "write", "exec", "git"],
    )

    assert composition.source == "local"
    assert "no project-specific context" in composition.system_prompt
    assert "builder" == composition.persona_label
    assert composition.context_doc_refs == []


@pytest.mark.asyncio
async def test_assembler_inlines_workspace_state_into_prompt():
    """workspace_state goes straight into the system prompt so weak
    LLMs don't have to remember to file_read workspace.md before
    acting."""
    assembler = PromptAssembler(default_template_registry())
    composition = await assembler.compose(
        _fake_run(),
        NoopKnowledgeClient(),
        intent_summary="Add user signup",
        tools_available=["file_read", "file_write"],
        workspace_state=[
            {"path": "backend/package.json", "size": 689},
            {"path": "backend/src/routes.ts", "size": 196},
        ],
    )
    assert "CURRENT WORKSPACE" in composition.system_prompt
    assert "backend/package.json" in composition.system_prompt
    assert "backend/src/routes.ts" in composition.system_prompt
    assert "Do NOT recreate" in composition.system_prompt


@pytest.mark.asyncio
async def test_assembler_inlines_prior_iterations_with_files():
    """prior_iterations lists what each previous phase produced so the
    worker doesn't pick a directive that overlaps."""
    assembler = PromptAssembler(default_template_registry())
    composition = await assembler.compose(
        _fake_run(),
        NoopKnowledgeClient(),
        intent_summary="Build the frontend",
        tools_available=["file_read", "file_write"],
        prior_iterations=[
            {
                "name": "Scaffold Backend API",
                "founder_summary": "Wrote 8 backend files.",
                "files_written": ["backend/package.json", "backend/src/routes.ts"],
            },
        ],
    )
    assert "PRIOR ITERATIONS" in composition.system_prompt
    assert "Scaffold Backend API" in composition.system_prompt
    assert "backend/package.json" in composition.system_prompt
    assert "Do NOT reproduce work" in composition.system_prompt


@pytest.mark.asyncio
async def test_assembler_first_iteration_says_workspace_empty():
    """workspace_state=[] (first iteration) gets a clear hint instead
    of a confusing empty list."""
    assembler = PromptAssembler(default_template_registry())
    composition = await assembler.compose(
        _fake_run(),
        NoopKnowledgeClient(),
        intent_summary="Build it",
        tools_available=["file_write"],
        workspace_state=[],
    )
    assert "first iteration" in composition.system_prompt


@pytest.mark.asyncio
async def test_assembler_marks_source_bsage_when_fragments_present():
    fragments = [
        KnowledgeFragment(
            path="notes/auth.md",
            title="Auth approach",
            excerpt="We use Supabase JWT with RLS.",
            score=0.92,
        )
    ]
    assembler = PromptAssembler(default_template_registry())
    composition = await assembler.compose(
        _fake_run(),
        FakeKnowledgeClient(fragments=fragments),
        intent_summary="Add auth to the settings page",
        tools_available=["read", "write"],
    )

    assert composition.source == "bsage"
    assert "Supabase JWT" in composition.system_prompt
    assert len(composition.context_doc_refs) == 1
    assert composition.context_doc_refs[0]["path"] == "notes/auth.md"


@pytest.mark.asyncio
async def test_assembler_is_deterministic_for_same_inputs():
    fragments = [
        KnowledgeFragment(path="a.md", title="A", excerpt="aaa", score=0.5),
        KnowledgeFragment(path="b.md", title="B", excerpt="bbb", score=0.4),
    ]
    assembler = PromptAssembler(default_template_registry())
    c1 = await assembler.compose(
        _fake_run(),
        FakeKnowledgeClient(fragments=fragments),
        intent_summary="Refactor the login flow",
        tools_available=["read", "write", "exec"],
    )
    c2 = await assembler.compose(
        _fake_run(),
        FakeKnowledgeClient(fragments=fragments),
        intent_summary="Refactor the login flow",
        tools_available=["read", "write", "exec"],
    )
    assert c1.system_prompt == c2.system_prompt
    assert c1.persona_label == c2.persona_label


def test_template_picker_prefers_matching_keywords():
    registry = PersonaTemplateRegistry(
        [
            PersonaTemplate(
                name="docs-writer",
                system_prompt_template="Writes docs.",
                tools=["read", "write"],
                keywords=["document", "changelog"],
            ),
            PersonaTemplate(
                name="builder",
                system_prompt_template="Builds features.",
                tools=["read", "write", "exec"],
                keywords=["implement", "build", "refactor"],
            ),
        ]
    )

    picked = registry.pick(
        "Refactor the login flow and add the tests",
        ["read", "write", "exec"],
    )
    assert picked.name == "builder"


def test_empty_registry_raises():
    with pytest.raises(LookupError):
        PersonaTemplateRegistry().pick("anything", [])


def test_knowledge_fragment_to_ref_hashes_excerpt():
    frag = KnowledgeFragment(
        path="p",
        title="T",
        excerpt="lorem ipsum " * 10,
        score=0.7,
    )
    ref = frag.to_ref()
    assert ref["path"] == "p"
    assert ref["score"] == 0.7
    assert len(ref["excerpt_hash"]) == 16
