"""PR8 — title-extractor prefers Request.intent_summary over LLM
chat-preamble sentences.

PR7 baseline collection on 2026-05-08 produced deliverable titles
like:

- "I'll skip the workspace context-read step and directly proceed..."
- "I'll build a tiny FastAPI app with a test as requested."
- "I see that the `artifact_list` tool is not available or failed."

These are LLM future-tense / reaction preamble — useless as a
deliverable card title. Founder's ``Request.intent_summary`` ("Build a
tiny FastAPI app...") is far more descriptive.

Fix: ``_first_sentence`` skips leading preamble lines and the title
falls through to ``intent_summary`` when only preamble was emitted.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from backend.src.core.run_artifacts import publish_run_output
from backend.src.models import Deliverable, ExecutionRun, Project, Request, RequestStatus, RunStatus


async def _seed(db_session, tenant_id, *, intent: str, reply: str) -> ExecutionRun:
    project = Project(tenant_id=tenant_id, name="P", description="")
    db_session.add(project)
    await db_session.flush()
    request = Request(
        tenant_id=tenant_id,
        project_id=project.id,
        intent_summary=intent,
        status=RequestStatus.open,
    )
    db_session.add(request)
    await db_session.flush()
    run = ExecutionRun(
        tenant_id=tenant_id,
        project_id=project.id,
        request_id=request.id,
        status=RunStatus.done,
        output_type="text",
        output_ref={"inline": reply},
    )
    db_session.add(run)
    await db_session.commit()
    await db_session.refresh(run)
    return run


@pytest.mark.parametrize(
    "preamble",
    [
        "I'll build a tiny FastAPI app with a test as requested.",
        "I'm going to start by reading the workspace.",
        "Let me look at the existing files first.",
        "Sure, I'll handle that.",
        "Got it, I'll skip the smoke step.",
        "First, I'll set up the structure.",
        "I will write the test file now.",
        "I see that the artifact_list tool is not available.",
        "Looking at the workspace, I'll begin by writing add.py.",
        "I need to build a FastAPI app with a test.",
        "I should start with the structure.",
        "First, I need to read stack.md.",
    ],
)
@pytest.mark.asyncio
async def test_title_skips_preamble_when_intent_summary_available(
    db_session, mock_tenant_id, seeded_tenant, preamble: str
) -> None:
    intent = "Build a FastAPI hello endpoint and pytest"
    run = await _seed(db_session, mock_tenant_id, intent=intent, reply=preamble)
    await publish_run_output(run, db_session)
    await db_session.commit()

    deliverable = (
        await db_session.execute(select(Deliverable).where(Deliverable.project_id == run.project_id))
    ).scalar_one()
    # The title should NOT be the preamble line.
    assert preamble not in deliverable.title
    # And SHOULD reflect the founder's intent.
    assert deliverable.title.startswith("Build a FastAPI hello endpoint")


@pytest.mark.asyncio
async def test_title_always_prefers_intent_summary_over_reply(db_session, mock_tenant_id, seeded_tenant) -> None:
    """PR8 round 2 — title NEVER reads the LLM reply when intent_summary
    exists, even if the reply has a substantive non-preamble sentence.

    Rationale: local LLMs hallucinate language (qwen3-coder under
    certain prompts answers in Chinese to English questions, etc.)
    and we don't want a hallucinated reply leaking into the
    deliverable card title — a system-critical surface. Founder's
    intent_summary is the language-stable source of truth."""
    intent = "Build a FastAPI hello endpoint and pytest"
    reply = "Shipped a hello app with FastAPI + pytest. All tests pass."
    run = await _seed(db_session, mock_tenant_id, intent=intent, reply=reply)
    await publish_run_output(run, db_session)
    await db_session.commit()

    deliverable = (
        await db_session.execute(select(Deliverable).where(Deliverable.project_id == run.project_id))
    ).scalar_one()
    # NOT "Shipped a hello app" — intent_summary always wins.
    assert deliverable.title == intent


@pytest.mark.asyncio
async def test_title_falls_through_to_reply_only_when_intent_and_directive_missing(
    db_session, mock_tenant_id, seeded_tenant
) -> None:
    """The reply-substantive-sentence fallback exists for orphan runs
    that have neither intent_summary nor directive. Pin the
    fallback so a future refactor doesn't drop it entirely."""
    project = Project(tenant_id=mock_tenant_id, name="P", description="")
    db_session.add(project)
    await db_session.flush()
    request = Request(
        tenant_id=mock_tenant_id,
        project_id=project.id,
        intent_summary="",  # neither intent
        status=RequestStatus.open,
    )
    db_session.add(request)
    await db_session.flush()
    run = ExecutionRun(
        tenant_id=mock_tenant_id,
        project_id=project.id,
        request_id=request.id,
        status=RunStatus.done,
        directive=None,  # nor directive
        output_type="text",
        output_ref={"inline": "Built `app.py`. Tests pass."},
    )
    db_session.add(run)
    await db_session.commit()
    await db_session.refresh(run)

    await publish_run_output(run, db_session)
    await db_session.commit()

    deliverable = (
        await db_session.execute(select(Deliverable).where(Deliverable.project_id == run.project_id))
    ).scalar_one()
    # Last-resort: reply substantive sentence wins when nothing else
    # is available.
    assert deliverable.title.startswith("Built `app.py`")


@pytest.mark.asyncio
async def test_title_falls_back_to_directive_when_no_intent_or_substantive_sentence(
    db_session, mock_tenant_id, seeded_tenant
) -> None:
    """If everything is preamble AND no intent_summary, use the run's
    directive (planner phase prompt)."""
    project = Project(tenant_id=mock_tenant_id, name="P", description="")
    db_session.add(project)
    await db_session.flush()
    request = Request(
        tenant_id=mock_tenant_id,
        project_id=project.id,
        intent_summary="",  # no intent
        status=RequestStatus.open,
    )
    db_session.add(request)
    await db_session.flush()
    run = ExecutionRun(
        tenant_id=mock_tenant_id,
        project_id=project.id,
        request_id=request.id,
        status=RunStatus.done,
        directive="Set up scaffolding for the new module",
        output_type="text",
        output_ref={"inline": "I'll do this now."},
    )
    db_session.add(run)
    await db_session.commit()
    await db_session.refresh(run)

    await publish_run_output(run, db_session)
    await db_session.commit()

    deliverable = (
        await db_session.execute(select(Deliverable).where(Deliverable.project_id == run.project_id))
    ).scalar_one()
    assert deliverable.title.startswith("Set up scaffolding")
