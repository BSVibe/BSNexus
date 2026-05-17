"""G9 — production orchestrator tests.

Covers the autonomous-loop wiring the greenfield rebuild was missing:
``plan_and_dispatch_request`` (front half), ``advance_request_after_proof``
(back half), ``provision_workspace``, and the ``RequestWorker`` message
handler. The LLM boundary is a stub executor — same pattern as the
benchmark-bridge tests — so these exercise the orchestration, not the
model.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from backend.src.core.domain import (
    DeliverableType,
    ProofState,
    RequestStatus,
    WorkPlanCreatedBy,
    WorkStepStatus,
)
from backend.src.core.orchestration import (
    advance_request_after_proof,
    plan_and_dispatch_request,
    provision_workspace,
)
from backend.src.core.work_steps import WorkStepDraft, create_work_plan
from backend.src.models import Deliverable, Project, Request, Tenant, WorkPlan, WorkStep
from backend.src.models.project import WorkspaceType
from backend.src.workers.request_worker import RequestWorker


# ───────────────────────────── stub executor ─────────────────────────────


@dataclass
class _StubExecutor:
    """Returns one ``file_write`` tool call, then stops — enough for
    ``dispatch_run_attempt`` to produce a Deliverable."""

    calls: list[dict[str, Any]] = field(default_factory=list)

    async def execute(
        self,
        *,
        messages: list[dict[str, Any]],
        metadata: dict[str, Any],
        model: str,
        workspace_dir: str | None = None,
        mcp_servers: dict[str, Any] | None = None,
        tools: list[dict[str, Any]] | None = None,
        on_chunk: Callable[[str], Awaitable[None]] | None = None,
        temperature: float | None = None,
    ) -> dict[str, Any]:
        self.calls.append({"model": model})
        if not any(m.get("role") == "tool" for m in messages):
            return {
                "output_type": "text",
                "output_ref": "",
                "actual_cost_cents": 0,
                "finish_reason": "tool_calls",
                "tool_calls": [
                    {
                        "id": "call_g9_write",
                        "name": "file_write",
                        "arguments": {
                            "path": "src/g9_marker.py",
                            "content": "def g9() -> str:\n    return 'orchestrated'\n",
                        },
                    }
                ],
            }
        return {
            "output_type": "text",
            "output_ref": "Done — wrote src/g9_marker.py.",
            "actual_cost_cents": 0,
            "finish_reason": "stop",
            "tool_calls": None,
        }


async def _seed_project(db_session, tenant_id: uuid.UUID, *, workspace_dir: str | None) -> Project:
    project = Project(
        tenant_id=tenant_id,
        name="g9-orch",
        description="",
        # A custom workspace_dir is the local_import shape. A
        # server_managed project's dir is always the lazily-assigned
        # ``<workspace_root>/<id>`` — provision_workspace re-provisions
        # a server_managed dir that points outside workspace_root.
        workspace_type=WorkspaceType.local_import if workspace_dir else WorkspaceType.server_managed,
        workspace_dir=workspace_dir,
    )
    db_session.add(project)
    await db_session.commit()
    await db_session.refresh(project)
    return project


async def _seed_open_request(db_session, tenant_id: uuid.UUID, project: Project) -> Request:
    request = Request(
        tenant_id=tenant_id,
        project_id=project.id,
        intent="Add src/g9_marker.py with a g9() stub.",
        status=RequestStatus.open,
    )
    db_session.add(request)
    await db_session.commit()
    await db_session.refresh(request)
    return request


# ───────────────────────────── provision_workspace ─────────────────────────


def test_provision_workspace_creates_managed_dir_when_absent(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("backend.src.core.orchestration.app_settings.workspace_root", str(tmp_path / "ws"))
    project = Project(
        id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        name="p",
        description="",
        workspace_type=WorkspaceType.server_managed,
        workspace_dir=None,
    )
    path = provision_workspace(project)
    assert path.exists() and path.is_dir()
    assert str(project.id) in str(path)


def test_provision_workspace_uses_explicit_dir(tmp_path) -> None:
    explicit = tmp_path / "explicit-ws"
    project = Project(
        id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        name="p",
        description="",
        workspace_type=WorkspaceType.local_import,
        workspace_dir=str(explicit),
    )
    path = provision_workspace(project)
    assert path == explicit
    assert path.exists()


def test_provision_workspace_seeds_default_agents_md(tmp_path) -> None:
    project = Project(
        id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        name="p",
        description="",
        workspace_type=WorkspaceType.local_import,
        workspace_dir=str(tmp_path / "ws"),
    )
    path = provision_workspace(project)
    agents_md = path / "AGENTS.md"
    assert agents_md.is_file()
    assert "Test-first" in agents_md.read_text()


def test_provision_workspace_preserves_existing_agents_md(tmp_path) -> None:
    """Once the founder (or the project's repo) owns AGENTS.md, a later
    provision call must not clobber it."""
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "AGENTS.md").write_text("# my own conventions\n")
    project = Project(
        id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        name="p",
        description="",
        workspace_type=WorkspaceType.local_import,
        workspace_dir=str(ws),
    )
    provision_workspace(project)
    assert (ws / "AGENTS.md").read_text() == "# my own conventions\n"


# ───────────────────────── plan_and_dispatch_request ───────────────────────


@pytest.mark.asyncio
async def test_plan_and_dispatch_creates_plan_and_runs(db_session, mock_tenant_id, seeded_tenant, tmp_path) -> None:
    project = await _seed_project(db_session, mock_tenant_id, workspace_dir=str(tmp_path / "ws"))
    request = await _seed_open_request(db_session, mock_tenant_id, project)

    await plan_and_dispatch_request(
        request_id=request.id,
        tenant_id=mock_tenant_id,
        session=db_session,
        stream_manager=AsyncMock(),
        executor=_StubExecutor(),
        executor_kind="injected",
        model="stub-model",
    )

    await db_session.refresh(request)
    # create_work_plan flips open → running.
    assert request.status == RequestStatus.running
    plan = (await db_session.execute(select(WorkPlan).where(WorkPlan.request_id == request.id))).scalar_one()
    steps = (await db_session.execute(select(WorkStep).where(WorkStep.plan_id == plan.id))).scalars().all()
    assert len(steps) == 1
    # A Deliverable was produced + the workspace file landed.
    deliverables = (
        (await db_session.execute(select(Deliverable).where(Deliverable.request_id == request.id))).scalars().all()
    )
    assert len(deliverables) == 1
    assert (tmp_path / "ws" / "src" / "g9_marker.py").exists()


@pytest.mark.asyncio
async def test_plan_and_dispatch_idempotent_on_non_open_request(
    db_session, mock_tenant_id, seeded_tenant, tmp_path
) -> None:
    project = await _seed_project(db_session, mock_tenant_id, workspace_dir=str(tmp_path / "ws"))
    request = await _seed_open_request(db_session, mock_tenant_id, project)
    request.status = RequestStatus.running  # already picked up
    await db_session.commit()

    stub = _StubExecutor()
    await plan_and_dispatch_request(
        request_id=request.id,
        tenant_id=mock_tenant_id,
        session=db_session,
        stream_manager=AsyncMock(),
        executor=stub,
        executor_kind="injected",
        model="stub-model",
    )
    # Skipped — no plan created, executor never called.
    assert stub.calls == []
    plans = (await db_session.execute(select(WorkPlan).where(WorkPlan.request_id == request.id))).scalars().all()
    assert plans == []


@pytest.mark.asyncio
async def test_plan_and_dispatch_cross_tenant_raises_lookup(
    db_session, mock_tenant_id, seeded_tenant, tmp_path
) -> None:
    other = uuid.uuid4()
    db_session.add(Tenant(id=other, name="other", slug=f"o-{other.hex[:8]}", owner_user_id="other-user"))
    await db_session.commit()
    project = await _seed_project(db_session, other, workspace_dir=str(tmp_path / "ws"))
    request = await _seed_open_request(db_session, other, project)

    with pytest.raises(LookupError):
        await plan_and_dispatch_request(
            request_id=request.id,
            tenant_id=mock_tenant_id,  # caller tenant ≠ request tenant
            session=db_session,
            stream_manager=AsyncMock(),
            executor=_StubExecutor(),
            executor_kind="injected",
            model="stub-model",
        )


@pytest.mark.asyncio
async def test_plan_and_dispatch_provisions_managed_workspace(
    db_session, mock_tenant_id, seeded_tenant, tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr("backend.src.core.orchestration.app_settings.workspace_root", str(tmp_path / "managed"))
    project = await _seed_project(db_session, mock_tenant_id, workspace_dir=None)
    request = await _seed_open_request(db_session, mock_tenant_id, project)

    await plan_and_dispatch_request(
        request_id=request.id,
        tenant_id=mock_tenant_id,
        session=db_session,
        stream_manager=AsyncMock(),
        executor=_StubExecutor(),
        executor_kind="injected",
        model="stub-model",
    )
    await db_session.refresh(project)
    # workspace_dir was lazily assigned + persisted.
    assert project.workspace_dir is not None
    assert str(project.id) in project.workspace_dir
    assert Path(project.workspace_dir).exists()


# ───────────────────────── G10 decomposer wiring ──────────────────────────


@dataclass
class _DecomposerStubExecutor:
    """Tracks separate calls for the decompose phase vs the work phase.

    First ``decompose_phase_responses`` calls (when ``tools is None``)
    return the queued plan text. After the queue empties or once tools
    are present (work phase), behaves like ``_StubExecutor``.
    """

    decompose_phase_responses: list[str] = field(default_factory=list)
    decompose_calls: int = 0
    work_calls: int = 0

    async def execute(
        self,
        *,
        messages: list[dict[str, Any]],
        metadata: dict[str, Any],
        model: str,
        workspace_dir: str | None = None,
        mcp_servers: dict[str, Any] | None = None,
        tools: list[dict[str, Any]] | None = None,
        on_chunk: Callable[[str], Awaitable[None]] | None = None,
        temperature: float | None = None,
    ) -> dict[str, Any]:
        # Decompose phase = tools is None and we have a queued response.
        if tools is None and self.decompose_phase_responses:
            self.decompose_calls += 1
            text = self.decompose_phase_responses.pop(0)
            return {
                "output_type": "text",
                "output_ref": text,
                "actual_cost_cents": 0,
                "finish_reason": "stop",
                "tool_calls": None,
            }
        # Work phase — same shape as _StubExecutor.
        self.work_calls += 1
        if not any(m.get("role") == "tool" for m in messages):
            return {
                "output_type": "text",
                "output_ref": "",
                "actual_cost_cents": 0,
                "finish_reason": "tool_calls",
                "tool_calls": [
                    {
                        "id": f"call_{self.work_calls}",
                        "name": "file_write",
                        "arguments": {
                            "path": f"step_{self.work_calls}.py",
                            "content": f"# step {self.work_calls}\n",
                        },
                    }
                ],
            }
        return {
            "output_type": "text",
            "output_ref": f"Done — step {self.work_calls}",
            "actual_cost_cents": 0,
            "finish_reason": "stop",
            "tool_calls": None,
        }


@pytest.mark.asyncio
async def test_plan_and_dispatch_uses_decomposer_for_multi_step(
    db_session, mock_tenant_id, seeded_tenant, tmp_path
) -> None:
    """When the decomposer returns 3 steps, the WorkPlan has 3 WorkSteps
    and each gets its own RunAttempt + Deliverable."""
    import json as _json

    project = await _seed_project(db_session, mock_tenant_id, workspace_dir=str(tmp_path / "ws"))
    request = await _seed_open_request(db_session, mock_tenant_id, project)

    decomposed = _json.dumps(
        [
            {"name": "Schema", "objective": "schema", "expected_outputs": ["schema.py"]},
            {"name": "API", "objective": "api", "expected_outputs": ["api.py"]},
            {"name": "UI", "objective": "ui", "expected_outputs": ["ui.tsx"]},
        ]
    )
    executor = _DecomposerStubExecutor(decompose_phase_responses=[decomposed])

    await plan_and_dispatch_request(
        request_id=request.id,
        tenant_id=mock_tenant_id,
        session=db_session,
        stream_manager=AsyncMock(),
        executor=executor,
        executor_kind="injected",
        model="stub-model",
    )

    # Decomposer fired exactly once.
    assert executor.decompose_calls == 1
    plan = (await db_session.execute(select(WorkPlan).where(WorkPlan.request_id == request.id))).scalar_one()
    assert plan.created_by == WorkPlanCreatedBy.llm_assisted
    steps = (await db_session.execute(select(WorkStep).where(WorkStep.plan_id == plan.id))).scalars().all()
    assert {s.name for s in steps} == {"Schema", "API", "UI"}
    # Each step ran (one work loop per step).
    deliverables = (
        (await db_session.execute(select(Deliverable).where(Deliverable.request_id == request.id))).scalars().all()
    )
    assert len(deliverables) == 3


@pytest.mark.asyncio
async def test_plan_and_dispatch_falls_back_when_decomposer_returns_garbage(
    db_session, mock_tenant_id, seeded_tenant, tmp_path
) -> None:
    """Garbage from the decomposer must not break dispatch — the plan
    degrades to G9 single-step (one WorkStep, one Deliverable)."""
    project = await _seed_project(db_session, mock_tenant_id, workspace_dir=str(tmp_path / "ws"))
    request = await _seed_open_request(db_session, mock_tenant_id, project)

    # Two garbage responses (initial + retry) → decomposer returns fallback.
    executor = _DecomposerStubExecutor(decompose_phase_responses=["not json", "still not json"])

    await plan_and_dispatch_request(
        request_id=request.id,
        tenant_id=mock_tenant_id,
        session=db_session,
        stream_manager=AsyncMock(),
        executor=executor,
        executor_kind="injected",
        model="stub-model",
    )

    assert executor.decompose_calls == 2  # initial + one retry
    plan = (await db_session.execute(select(WorkPlan).where(WorkPlan.request_id == request.id))).scalar_one()
    steps = (await db_session.execute(select(WorkStep).where(WorkStep.plan_id == plan.id))).scalars().all()
    assert len(steps) == 1


# ───────────────────────── advance_request_after_proof ─────────────────────


async def _seed_request_at_verifying(
    db_session, tenant_id: uuid.UUID, *, proof_state: ProofState
) -> tuple[Request, WorkStep, Deliverable, Project]:
    project = await _seed_project(db_session, tenant_id, workspace_dir="/tmp/ignored")
    request = Request(
        tenant_id=tenant_id,
        project_id=project.id,
        intent="x",
        status=RequestStatus.running,
    )
    db_session.add(request)
    await db_session.commit()
    await db_session.refresh(request)
    plan = await create_work_plan(
        request=request,
        steps=[WorkStepDraft(name="step", objective="x", expected_outputs=[])],
        created_by=WorkPlanCreatedBy.system,
        session=db_session,
    )
    step = (await db_session.execute(select(WorkStep).where(WorkStep.plan_id == plan.id))).scalar_one()
    step.status = WorkStepStatus.verifying
    deliverable = Deliverable(
        tenant_id=tenant_id,
        project_id=project.id,
        request_id=request.id,
        work_step_id=step.id,
        type=DeliverableType.code,
        title="d",
        artifact_refs=[],
        proof_state=proof_state,
    )
    db_session.add(deliverable)
    await db_session.commit()
    await db_session.refresh(deliverable)
    await db_session.refresh(step)
    await db_session.refresh(request)
    return request, step, deliverable, project


@pytest.mark.asyncio
async def test_advance_verified_ships_request(db_session, mock_tenant_id, seeded_tenant) -> None:
    request, step, deliverable, _ = await _seed_request_at_verifying(
        db_session, mock_tenant_id, proof_state=ProofState.verified
    )
    await advance_request_after_proof(deliverable=deliverable, session=db_session, stream_manager=None)
    await db_session.refresh(step)
    await db_session.refresh(request)
    assert step.status == WorkStepStatus.review_ready
    # Single-step, all review_ready, all deliverables verified → shipped.
    assert request.status == RequestStatus.shipped


@pytest.mark.asyncio
async def test_advance_failed_proof_routes_request_to_needs_decision(db_session, mock_tenant_id, seeded_tenant) -> None:
    """A failed-proof WorkStep no longer dead-ends the Request at
    ``blocked``: the Request moves to ``needs_decision`` and a blocking
    founder Decision is raised so the founder has recourse."""
    from backend.src.models import Decision

    request, step, deliverable, _ = await _seed_request_at_verifying(
        db_session, mock_tenant_id, proof_state=ProofState.verification_failed
    )
    await advance_request_after_proof(deliverable=deliverable, session=db_session, stream_manager=None)
    await db_session.refresh(step)
    await db_session.refresh(request)
    assert step.status == WorkStepStatus.failed
    assert request.status == RequestStatus.needs_decision

    decisions = (await db_session.execute(select(Decision).where(Decision.request_id == request.id))).scalars().all()
    assert len(decisions) == 1
    assert decisions[0].blocking is True
    assert decisions[0].work_step_id == step.id
    assert decisions[0].options == ["retry", "reframe"]


@pytest.mark.asyncio
async def test_advance_does_not_finalize_with_step_in_flight(db_session, mock_tenant_id, seeded_tenant) -> None:
    """Two-step plan: one verifies, the other is still running → the
    Request stays ``running``."""
    project = await _seed_project(db_session, mock_tenant_id, workspace_dir="/tmp/ignored")
    request = Request(
        tenant_id=mock_tenant_id,
        project_id=project.id,
        intent="two-step",
        status=RequestStatus.running,
    )
    db_session.add(request)
    await db_session.commit()
    await db_session.refresh(request)
    plan = await create_work_plan(
        request=request,
        steps=[
            WorkStepDraft(name="a", objective="a", expected_outputs=[]),
            WorkStepDraft(name="b", objective="b", expected_outputs=[]),
        ],
        created_by=WorkPlanCreatedBy.system,
        session=db_session,
    )
    steps = (await db_session.execute(select(WorkStep).where(WorkStep.plan_id == plan.id))).scalars().all()
    step_a, step_b = steps
    step_a.status = WorkStepStatus.verifying
    step_b.status = WorkStepStatus.running
    deliverable_a = Deliverable(
        tenant_id=mock_tenant_id,
        project_id=project.id,
        request_id=request.id,
        work_step_id=step_a.id,
        type=DeliverableType.code,
        title="a",
        artifact_refs=[],
        proof_state=ProofState.verified,
    )
    db_session.add(deliverable_a)
    await db_session.commit()
    await db_session.refresh(deliverable_a)

    await advance_request_after_proof(deliverable=deliverable_a, session=db_session, stream_manager=None)
    await db_session.refresh(step_a)
    await db_session.refresh(request)
    assert step_a.status == WorkStepStatus.review_ready
    # step_b still running → request not finalized.
    assert request.status == RequestStatus.running


# ───────────────────────────── RequestWorker ─────────────────────────────


@pytest.mark.asyncio
async def test_request_worker_bad_message_is_acked_and_skipped() -> None:
    stream_manager = AsyncMock()
    worker = RequestWorker(stream_manager=stream_manager, session_factory=AsyncMock())
    await worker._handle_message({"_message_id": "1-0", "garbage": "yes"})
    # Bad message → acked so it doesn't pin the consumer, never processed.
    stream_manager.acknowledge.assert_awaited_once()


@pytest.mark.asyncio
async def test_request_worker_good_message_processed(
    db_session, mock_tenant_id, seeded_tenant, tmp_path, monkeypatch
) -> None:
    project = await _seed_project(db_session, mock_tenant_id, workspace_dir=str(tmp_path / "ws"))
    request = await _seed_open_request(db_session, mock_tenant_id, project)

    stream_manager = AsyncMock()

    # Session factory yields the test session; executor is stubbed via a
    # monkeypatched plan_and_dispatch_request to keep this test scoped to
    # the worker's message-handling contract.
    seen: dict[str, Any] = {}

    async def _fake_plan_and_dispatch(*, request_id, tenant_id, session, stream_manager):
        seen["request_id"] = request_id
        seen["tenant_id"] = tenant_id

    monkeypatch.setattr("backend.src.workers.request_worker.plan_and_dispatch_request", _fake_plan_and_dispatch)

    class _SessionCtx:
        async def __aenter__(self):
            return db_session

        async def __aexit__(self, *exc):
            return False

    worker = RequestWorker(stream_manager=stream_manager, session_factory=lambda: _SessionCtx())
    await worker._handle_message(
        {
            "_message_id": "2-0",
            "request_id": str(request.id),
            "tenant_id": str(mock_tenant_id),
        }
    )
    assert seen["request_id"] == request.id
    assert seen["tenant_id"] == mock_tenant_id
    stream_manager.acknowledge.assert_awaited_once()


# ──────────────────── directions endpoint enqueue (G9) ────────────────────


@pytest.mark.asyncio
async def test_post_direction_enqueues_request_on_queue(
    client, db_session, mock_tenant_id, seeded_tenant, mock_stream_manager
) -> None:
    """A Direction that resolves to a Request must be handed to the
    RequestWorker via ``request:queue`` — without it the autonomous
    loop never starts."""
    project = Project(tenant_id=mock_tenant_id, name="enqueue-test", description="")
    db_session.add(project)
    await db_session.commit()
    await db_session.refresh(project)

    resp = await client.post(
        "/api/v1/directions",
        json={
            "project_id": str(project.id),
            "source": "web",
            "body": "Add a healthz route",
        },
        headers={"Authorization": "Bearer fake"},
    )
    assert resp.status_code == 201, resp.text
    request_id = resp.json()["request"]["id"]

    # Published exactly once to request:queue with the new Request id.
    publish_calls = [c for c in mock_stream_manager.publish.await_args_list if c.args and c.args[0] == "request:queue"]
    assert len(publish_calls) == 1
    payload = publish_calls[0].args[1]
    assert payload["request_id"] == request_id
    assert payload["tenant_id"] == str(mock_tenant_id)


@pytest.mark.asyncio
async def test_post_direction_greenfield_autocreates_project(
    client, db_session, mock_tenant_id, seeded_tenant, mock_stream_manager
) -> None:
    """A founder's first Direction — no project_id, zero existing
    projects — bootstraps a project from the Direction itself instead
    of dead-ending on a routing prompt with no options."""
    resp = await client.post(
        "/api/v1/directions",
        json={"source": "web", "body": "Build a small FastAPI task tracker"},
        headers={"Authorization": "Bearer fake"},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    # A Request was opened (not a routing prompt).
    assert body["routing"] is None
    assert body["request"] is not None

    # Exactly one project now exists, named from the Direction body.
    projects = (await db_session.execute(select(Project).where(Project.tenant_id == mock_tenant_id))).scalars().all()
    assert len(projects) == 1
    assert projects[0].name == "Build a small FastAPI task tracker"

    # The Request was enqueued so the autonomous loop starts.
    publish_calls = [c for c in mock_stream_manager.publish.await_args_list if c.args and c.args[0] == "request:queue"]
    assert len(publish_calls) == 1
    assert publish_calls[0].args[1]["request_id"] == body["request"]["id"]


@pytest.mark.asyncio
async def test_post_direction_routing_required_does_not_enqueue(
    client, db_session, mock_tenant_id, seeded_tenant, mock_stream_manager
) -> None:
    """A routing-required Direction has no Request yet — nothing to
    enqueue."""
    # Two projects + no project_id + no hint → routing required.
    for name in ("alpha", "beta"):
        db_session.add(Project(tenant_id=mock_tenant_id, name=name, description=""))
    await db_session.commit()

    resp = await client.post(
        "/api/v1/directions",
        json={"source": "web", "body": "ambiguous direction"},
        headers={"Authorization": "Bearer fake"},
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["routing"] is not None
    assert resp.json()["request"] is None

    request_queue_calls = [
        c for c in mock_stream_manager.publish.await_args_list if c.args and c.args[0] == "request:queue"
    ]
    assert request_queue_calls == []
