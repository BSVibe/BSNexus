"""PR8 stabilization — pin the order: deliverable row commits THEN
the verification envelope is enqueued.

The pre-PR8 code enqueued from inside ``publish_run_output`` against
an uncommitted row; the worker dequeued ~3ms later from a fresh
session and skipped the deliverable as missing
(``verifier_skipped_missing_deliverable`` log + ``proof_state`` stuck
at ``verification_missing``). This was caught during the PR7 baseline
collection runs on 2026-05-08.

The fix moved the enqueue out of ``publish_run_output`` and into the
dispatcher, AFTER ``session.commit()``. This test asserts the
ordering: ``stream_manager.publish`` is called only after the
deliverable row exists in a SEPARATE session.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from backend.src.core.run_artifacts import publish_run_output
from backend.src.core.verifier.enqueue import VERIFICATION_QUEUE_STREAM, maybe_enqueue_for_deliverable
from backend.src.models import Deliverable, ExecutionRun, Project, Request, RequestStatus, RunStatus


async def _seed(db_session, tenant_id, *, reply_text: str) -> ExecutionRun:
    project = Project(tenant_id=tenant_id, name="P", description="")
    db_session.add(project)
    await db_session.flush()
    request = Request(
        tenant_id=tenant_id,
        project_id=project.id,
        intent_summary="x",
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
        output_ref={"inline": reply_text},
    )
    db_session.add(run)
    await db_session.commit()
    await db_session.refresh(run)
    return run


@pytest.mark.asyncio
async def test_publish_run_output_does_not_enqueue_inline(db_session, mock_tenant_id, seeded_tenant) -> None:
    """``publish_run_output`` MUST NOT call ``stream_manager.publish``
    on its own — that's the race the dispatcher's post-commit hook
    fixed."""
    reply = 'Done.\n\n```bsnexus-verification\n{"verifier_type": "software_test", "command": ["true"]}\n```\n'
    run = await _seed(db_session, mock_tenant_id, reply_text=reply)
    stream_manager = AsyncMock()
    stream_manager.publish = AsyncMock(return_value="0-0")

    deliverable = await publish_run_output(run, db_session, stream_manager=stream_manager)

    assert deliverable is not None
    assert deliverable.verifier_type == "software_test"
    # CRITICAL: no enqueue happened from inside publish_run_output.
    stream_manager.publish.assert_not_awaited()


@pytest.mark.asyncio
async def test_post_commit_enqueue_sees_deliverable_in_fresh_session(
    db_session, test_session_maker, mock_tenant_id, seeded_tenant
) -> None:
    """Simulate the dispatcher's post-commit ordering and confirm a
    fresh session can read the deliverable the worker would query."""
    reply = 'Done.\n\n```bsnexus-verification\n{"verifier_type": "software_test", "command": ["true"]}\n```\n'
    run = await _seed(db_session, mock_tenant_id, reply_text=reply)
    stream_manager = AsyncMock()
    stream_manager.publish = AsyncMock(return_value="0-0")

    deliverable = await publish_run_output(run, db_session, stream_manager=stream_manager)
    assert deliverable is not None
    deliverable_id = deliverable.id

    # COMMIT FIRST.
    await db_session.commit()

    # NOW open a fresh session (mirrors the worker's _open_session) and
    # confirm the deliverable is visible. Pre-PR8 the worker did this
    # against an UNCOMMITTED row and got nothing back.
    async with test_session_maker() as worker_session:
        found = (
            await worker_session.execute(select(Deliverable).where(Deliverable.id == deliverable_id))
        ).scalar_one_or_none()
        assert found is not None
        assert found.verifier_type == "software_test"

    # Now mimic the dispatcher's post-commit enqueue and confirm the
    # envelope payload addresses the committed row.
    await maybe_enqueue_for_deliverable(stream_manager, deliverable)
    stream_manager.publish.assert_awaited_once()
    args, _ = stream_manager.publish.call_args
    assert args[0] == VERIFICATION_QUEUE_STREAM
    assert args[1]["deliverable_id"] == str(deliverable_id)
