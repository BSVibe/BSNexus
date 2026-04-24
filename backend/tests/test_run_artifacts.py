"""publish_run_output — materialise chat reply + deliverable rows."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from backend.src.core.run_artifacts import publish_run_output
from backend.src.models import (
    ConversationMessage,
    Deliverable,
    DeliverableStatus,
    DeliverableType,
    DeliverableVersion,
    ExecutionRun,
    Project,
    Request,
    RequestStatus,
    RunPriority,
    RunStatus,
)


async def _seed_completed_run(
    db_session,
    tenant_id,
    *,
    inline: str = "",
    files: list[dict] | None = None,
    directive: str | None = None,
):
    project = Project(tenant_id=tenant_id, name="art test", description="")
    db_session.add(project)
    await db_session.flush()

    request = Request(
        tenant_id=tenant_id,
        project_id=project.id,
        intent_summary="Build a tiny thing",
        status=RequestStatus.open,
    )
    db_session.add(request)
    await db_session.flush()

    run = ExecutionRun(
        tenant_id=tenant_id,
        project_id=project.id,
        request_id=request.id,
        status=RunStatus.done,
        priority=RunPriority.medium,
        directive=directive,
        output_ref={"inline": inline, "files": files or []},
    )
    db_session.add(run)
    await db_session.commit()
    await db_session.refresh(run)
    return run


@pytest.mark.asyncio
async def test_publishes_assistant_message_and_deliverable_from_tool_file_list(
    db_session, mock_tenant_id, seeded_tenant
):
    run = await _seed_completed_run(
        db_session,
        mock_tenant_id,
        inline="Shipped a hello app.",
        files=[
            {"path": "src/main.py", "size": 12, "language": "python"},
            {"path": "README.md", "size": 50, "language": "markdown"},
        ],
    )

    await publish_run_output(run, db_session)
    await db_session.flush()

    msg = (
        await db_session.execute(
            select(ConversationMessage).where(
                ConversationMessage.request_id == run.request_id,
                ConversationMessage.role == "assistant",
            )
        )
    ).scalar_one()
    assert msg.content == "Shipped a hello app."

    deliverable = (
        await db_session.execute(select(Deliverable).where(Deliverable.request_id == run.request_id))
    ).scalar_one()
    # Had a .py file → code type.
    assert deliverable.type == DeliverableType.code
    assert deliverable.status == DeliverableStatus.delivered
    assert deliverable.title.startswith("Shipped a hello app")

    version = (
        await db_session.execute(select(DeliverableVersion).where(DeliverableVersion.deliverable_id == deliverable.id))
    ).scalar_one()
    assert [f["path"] for f in version.content_ref["files"]] == [
        "src/main.py",
        "README.md",
    ]


@pytest.mark.asyncio
async def test_skips_entirely_when_run_produced_nothing(db_session, mock_tenant_id, seeded_tenant):
    run = await _seed_completed_run(db_session, mock_tenant_id, inline="", files=[])
    await publish_run_output(run, db_session)

    no_msg = (
        (await db_session.execute(select(ConversationMessage).where(ConversationMessage.request_id == run.request_id)))
        .scalars()
        .all()
    )
    assert no_msg == []
    no_deliverables = (
        (await db_session.execute(select(Deliverable).where(Deliverable.request_id == run.request_id))).scalars().all()
    )
    assert no_deliverables == []


@pytest.mark.asyncio
async def test_idempotent_on_second_call(db_session, mock_tenant_id, seeded_tenant):
    run = await _seed_completed_run(
        db_session,
        mock_tenant_id,
        inline="Second time should be a no-op.",
        files=[{"path": "a.txt", "size": 4}],
    )
    await publish_run_output(run, db_session)
    await db_session.flush()
    await publish_run_output(run, db_session)
    await db_session.flush()

    msgs = (
        (await db_session.execute(select(ConversationMessage).where(ConversationMessage.request_id == run.request_id)))
        .scalars()
        .all()
    )
    assert len(msgs) == 1

    deliverables = (
        (await db_session.execute(select(Deliverable).where(Deliverable.request_id == run.request_id))).scalars().all()
    )
    assert len(deliverables) == 1


@pytest.mark.asyncio
async def test_falls_back_to_file_list_summary_when_chat_empty(db_session, mock_tenant_id, seeded_tenant):
    run = await _seed_completed_run(
        db_session,
        mock_tenant_id,
        inline="",
        files=[{"path": "design.html", "size": 100, "language": "html"}],
    )
    await publish_run_output(run, db_session)
    await db_session.flush()

    msg = (
        await db_session.execute(
            select(ConversationMessage).where(
                ConversationMessage.request_id == run.request_id,
                ConversationMessage.role == "assistant",
            )
        )
    ).scalar_one()
    # Auto-summary references the file names.
    assert "design.html" in msg.content

    deliverable = (
        await db_session.execute(select(Deliverable).where(Deliverable.request_id == run.request_id))
    ).scalar_one()
    # .html → design type.
    assert deliverable.type == DeliverableType.design


@pytest.mark.asyncio
async def test_skips_for_runs_that_are_not_done(db_session, mock_tenant_id, seeded_tenant):
    run = await _seed_completed_run(db_session, mock_tenant_id, inline="text", files=[{"path": "f.txt", "size": 1}])
    run.status = RunStatus.running
    await db_session.flush()

    await publish_run_output(run, db_session)
    no_deliverables = (
        (await db_session.execute(select(Deliverable).where(Deliverable.request_id == run.request_id))).scalars().all()
    )
    assert no_deliverables == []


class _RecordingKnowledgeClient:
    def __init__(self):
        self.entries: list[dict] = []
        self.decisions: list[dict] = []

    async def search(self, intent, *, top_k=10):
        return []

    async def fetch(self, path):
        return None

    async def backlinks(self, path):
        return []

    async def index(self, **kwargs):
        self.entries.append(kwargs)
        from backend.src.core.composer import KnowledgeEntryRef

        return KnowledgeEntryRef(id="note-1", path="garden/idea/note-1.md")

    async def record_decision(self, **kwargs):
        self.decisions.append(kwargs)
        return None


@pytest.mark.asyncio
async def test_publishes_indexes_deliverable_to_knowledge_when_provided(db_session, mock_tenant_id, seeded_tenant):
    run = await _seed_completed_run(
        db_session,
        mock_tenant_id,
        inline="Built the TODO backend.",
        files=[{"path": "src/main.py", "size": 100, "language": "python"}],
    )
    client = _RecordingKnowledgeClient()

    await publish_run_output(run, db_session, knowledge=client)
    await db_session.flush()

    assert len(client.entries) == 1
    entry = client.entries[0]
    # No pre-computed title/content — the whole run payload is handed to
    # BSage under ``payload`` so the seed-refiner generates the title.
    assert "title" not in entry
    assert "content" not in entry
    payload = entry["payload"]
    assert payload["reply_text"] == "Built the TODO backend."
    assert payload["files"] == [{"path": "src/main.py", "size": 100, "language": "python"}]
    assert payload["run_id"] == str(run.id)
    assert payload["deliverable_type"] == "code"
    assert any(t.startswith("project:") for t in payload["tags"])
    assert "bsnexus-deliverable" in payload["tags"]


@pytest.mark.asyncio
async def test_publishes_forwards_originator_jwt_from_request(db_session, mock_tenant_id, seeded_tenant):
    run = await _seed_completed_run(
        db_session,
        mock_tenant_id,
        inline="Shipped.",
        files=[{"path": "a.py", "size": 1}],
    )
    req = (await db_session.execute(select(Request).where(Request.id == run.request_id))).scalar_one()
    req.originator_auth = "founder-jwt-xyz"
    await db_session.flush()

    client = _RecordingKnowledgeClient()
    await publish_run_output(run, db_session, knowledge=client)

    assert len(client.entries) == 1
    assert client.entries[0]["auth_token"] == "founder-jwt-xyz"


@pytest.mark.asyncio
async def test_publishes_skips_index_when_no_knowledge_client(db_session, mock_tenant_id, seeded_tenant):
    run = await _seed_completed_run(
        db_session,
        mock_tenant_id,
        inline="Built the thing.",
        files=[{"path": "a.py", "size": 1}],
    )
    # No knowledge kwarg → no index call. Deliverable still written.
    await publish_run_output(run, db_session)
    await db_session.flush()
    deliverable = (
        await db_session.execute(select(Deliverable).where(Deliverable.request_id == run.request_id))
    ).scalar_one()
    assert deliverable.title.startswith("Built the thing")
