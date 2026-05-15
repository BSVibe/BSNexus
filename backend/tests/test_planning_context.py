"""G10 — tests for ``build_project_context``.

The decomposer needs a substrate that captures *what the founder asked
for*, in enough surrounding context that the model can decide whether
to split the Request into multiple WorkSteps. PR-1 includes:

- Request intent (always)
- parent Direction body when ``origin_direction_id`` resolves
- BSage knowledge fragments when a non-Noop KnowledgeClient is passed

KnowledgeClient is a Protocol with a Noop fallback; the builder must
never raise when BSage is unreachable or absent.
"""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import AsyncMock

import pytest

from backend.src.core.composer.knowledge_client import (
    KnowledgeFragment,
    NoopKnowledgeClient,
)
from backend.src.core.domain import DirectionSource, RequestStatus
from backend.src.core.planning.context import ProjectContext, build_project_context
from backend.src.models import Direction, Project, Request
from backend.src.models.project import WorkspaceType


async def _seed_project(db_session, tenant_id: uuid.UUID) -> Project:
    project = Project(
        tenant_id=tenant_id,
        name="g10-ctx",
        description="",
        workspace_type=WorkspaceType.server_managed,
        workspace_dir=None,
    )
    db_session.add(project)
    await db_session.commit()
    await db_session.refresh(project)
    return project


async def _seed_direction(db_session, tenant_id, project, body: str) -> Direction:
    direction = Direction(
        tenant_id=tenant_id,
        project_id=project.id,
        source=DirectionSource.web,
        actor_id="test-user",
        body=body,
    )
    db_session.add(direction)
    await db_session.commit()
    await db_session.refresh(direction)
    return direction


async def _seed_request(
    db_session,
    tenant_id,
    project,
    intent: str,
    direction: Direction | None = None,
) -> Request:
    request = Request(
        tenant_id=tenant_id,
        project_id=project.id,
        intent=intent,
        status=RequestStatus.open,
        origin_direction_id=direction.id if direction else None,
    )
    db_session.add(request)
    await db_session.commit()
    await db_session.refresh(request)
    return request


@pytest.mark.asyncio
async def test_build_context_request_only(db_session, mock_tenant_id, seeded_tenant) -> None:
    project = await _seed_project(db_session, mock_tenant_id)
    request = await _seed_request(db_session, mock_tenant_id, project, "Build a healthz endpoint.")

    ctx = await build_project_context(request=request, session=db_session)

    assert ctx.request_intent == "Build a healthz endpoint."
    assert ctx.direction_body is None
    assert ctx.knowledge_fragments == ()


@pytest.mark.asyncio
async def test_build_context_with_direction(db_session, mock_tenant_id, seeded_tenant) -> None:
    project = await _seed_project(db_session, mock_tenant_id)
    direction = await _seed_direction(
        db_session,
        mock_tenant_id,
        project,
        "I want a small task tracker with a CLI and an HTTP API.",
    )
    request = await _seed_request(
        db_session,
        mock_tenant_id,
        project,
        "Build a task tracker.",
        direction=direction,
    )

    ctx = await build_project_context(request=request, session=db_session)

    assert ctx.request_intent == "Build a task tracker."
    assert ctx.direction_body is not None
    assert "task tracker" in ctx.direction_body


@pytest.mark.asyncio
async def test_build_context_with_knowledge_fragments(db_session, mock_tenant_id, seeded_tenant) -> None:
    project = await _seed_project(db_session, mock_tenant_id)
    request = await _seed_request(db_session, mock_tenant_id, project, "Add OAuth2 to the API.")

    knowledge_client = AsyncMock()
    knowledge_client.search = AsyncMock(
        return_value=[
            KnowledgeFragment(
                path="auth/oauth2.md",
                title="OAuth2 trap notes",
                excerpt="Round 5 cutover taught us …",
                score=0.91,
            ),
        ]
    )

    ctx = await build_project_context(request=request, session=db_session, knowledge_client=knowledge_client)

    assert len(ctx.knowledge_fragments) == 1
    assert ctx.knowledge_fragments[0].path == "auth/oauth2.md"
    knowledge_client.search.assert_awaited_once()


@pytest.mark.asyncio
async def test_build_context_with_noop_knowledge_client_is_empty(db_session, mock_tenant_id, seeded_tenant) -> None:
    project = await _seed_project(db_session, mock_tenant_id)
    request = await _seed_request(db_session, mock_tenant_id, project, "X.")

    ctx = await build_project_context(
        request=request,
        session=db_session,
        knowledge_client=NoopKnowledgeClient(),
    )

    assert ctx.knowledge_fragments == ()


@pytest.mark.asyncio
async def test_build_context_swallows_knowledge_errors(db_session, mock_tenant_id, seeded_tenant) -> None:
    """A flaky KnowledgeClient must never break the planning pipeline.

    BSage being down should degrade the context, not fail the dispatch.
    """
    project = await _seed_project(db_session, mock_tenant_id)
    request = await _seed_request(db_session, mock_tenant_id, project, "X.")

    knowledge_client: Any = AsyncMock()
    knowledge_client.search = AsyncMock(side_effect=RuntimeError("BSage down"))

    ctx = await build_project_context(request=request, session=db_session, knowledge_client=knowledge_client)

    assert ctx.knowledge_fragments == ()


def test_project_context_render_includes_all_sections() -> None:
    ctx = ProjectContext(
        request_intent="Add /healthz endpoint",
        direction_body="Build a small monitoring API.",
        knowledge_fragments=(
            KnowledgeFragment(
                path="ops/healthchecks.md",
                title="Healthcheck conventions",
                excerpt="Always return 200 with a JSON body.",
                score=0.7,
            ),
        ),
    )
    rendered = ctx.render()
    assert "Add /healthz endpoint" in rendered
    assert "Build a small monitoring API." in rendered
    assert "Healthcheck conventions" in rendered


def test_project_context_render_request_only_omits_empty_sections() -> None:
    ctx = ProjectContext(
        request_intent="Trivial fix",
        direction_body=None,
        knowledge_fragments=(),
    )
    rendered = ctx.render()
    assert "Trivial fix" in rendered
    # No section headers for the absent pieces — keep the prompt clean
    # so the LLM doesn't see "Direction: (none)" noise.
    assert "Direction:" not in rendered
    assert "Knowledge fragments" not in rendered
