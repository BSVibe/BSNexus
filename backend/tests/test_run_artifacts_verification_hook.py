"""``_ensure_deliverable`` stamps verifier_type / verifier_inputs from the
``bsnexus-verification`` fenced block (decision-locks A1 + PR6).

Drives the real ``publish_run_output`` path against the in-memory
SQLite test DB so we exercise:

- ``ExecutionRun.output_ref`` carrying a chat reply with the fenced block
- ``_ensure_deliverable`` creating the Deliverable + DeliverableVersion
- ``_attach_verification_from_reply`` parsing and resolving cwd
- the auto-enqueue hook calling ``maybe_enqueue_for_deliverable`` on the
  stream manager
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock

import pytest

from backend.src.core import project_workspace
from backend.src.core.run_artifacts import publish_run_output
from backend.src.core.verifier.enqueue import VERIFICATION_QUEUE_STREAM
from backend.src.models import (
    ExecutionRun,
    Project,
    ProofState,
    Request,
    RequestStatus,
    RunStatus,
)


async def _seed_run_with_reply(
    db_session,
    tenant_id: uuid.UUID,
    *,
    reply_text: str,
) -> ExecutionRun:
    project = Project(tenant_id=tenant_id, name="P", description="")
    db_session.add(project)
    await db_session.flush()
    request = Request(
        tenant_id=tenant_id,
        project_id=project.id,
        intent_summary="ship add(a,b)",
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
async def test_publish_run_output_stamps_verifier_type_from_fenced_block(
    db_session, mock_tenant_id, seeded_tenant
) -> None:
    reply = """\
Done — wrote add.py and tests/test_add.py. shell_exec exit=0.

```bsnexus-verification
{
  "verifier_type": "software_test",
  "command": ["python", "-m", "pytest", "tests/test_add.py", "-q"],
  "cwd": "tests",
  "timeout_s": 60
}
```
"""
    run = await _seed_run_with_reply(db_session, mock_tenant_id, reply_text=reply)
    stream_manager = AsyncMock()
    stream_manager.publish = AsyncMock(return_value="0-0")

    deliverable = await publish_run_output(run, db_session, stream_manager=stream_manager)
    await db_session.commit()

    assert deliverable is not None
    assert deliverable.verifier_type == "software_test"
    assert deliverable.verifier_inputs is not None
    assert deliverable.verifier_inputs["command"] == [
        "python",
        "-m",
        "pytest",
        "tests/test_add.py",
        "-q",
    ]
    assert deliverable.verifier_inputs["timeout_s"] == 60
    # cwd was relative; resolve_workspace_cwd should have absolutized it
    # against the project workspace root.
    expected_cwd = (project_workspace.project_workspace_path(run.project_id) / "tests").resolve()
    assert deliverable.verifier_inputs["cwd"] == str(expected_cwd)

    # PR8 race fix — ``publish_run_output`` no longer enqueues from
    # inside its session. The dispatcher commits then calls
    # ``maybe_enqueue_for_deliverable`` post-commit. Verify the
    # standalone helper behaves correctly when called against a
    # committed deliverable.
    from backend.src.core.verifier.enqueue import maybe_enqueue_for_deliverable  # noqa: PLC0415

    await maybe_enqueue_for_deliverable(stream_manager, deliverable)
    stream_manager.publish.assert_awaited_once()
    args, _ = stream_manager.publish.call_args
    assert args[0] == VERIFICATION_QUEUE_STREAM
    payload = args[1]
    assert payload["verifier_type"] == "software_test"
    assert payload["deliverable_id"] == str(deliverable.id)


@pytest.mark.asyncio
async def test_publish_run_output_skips_enqueue_when_block_missing(db_session, mock_tenant_id, seeded_tenant) -> None:
    """No fenced block ⇒ verifier_type stays None, no envelope enqueued.
    Deliverable still gets created with proof_state=verification_missing
    (the schema default). UI surfaces 'no proof'."""
    run = await _seed_run_with_reply(
        db_session,
        mock_tenant_id,
        reply_text="just a plain reply with no verification block",
    )
    stream_manager = AsyncMock()
    stream_manager.publish = AsyncMock(return_value="0-0")

    deliverable = await publish_run_output(run, db_session, stream_manager=stream_manager)
    await db_session.commit()

    assert deliverable is not None
    assert deliverable.verifier_type is None
    assert deliverable.verifier_inputs is None
    assert deliverable.proof_state == ProofState.verification_missing

    # No fenced block ⇒ ``publish_run_output`` returns the deliverable
    # but downstream enqueue logic treats verifier_type=None as a no-op.
    from backend.src.core.verifier.enqueue import maybe_enqueue_for_deliverable  # noqa: PLC0415

    await maybe_enqueue_for_deliverable(stream_manager, deliverable)
    stream_manager.publish.assert_not_awaited()


@pytest.mark.asyncio
async def test_deliverable_title_does_not_leak_verification_block(db_session, mock_tenant_id, seeded_tenant) -> None:
    """Regression: live-LLM run 2026-05-08 produced a deliverable with
    ``title = "bsnexus-verification"`` because ``_first_sentence`` walked
    into the fenced block when the LLM's reply had no other prose.
    ``publish_run_output`` strips the block before title derivation now;
    the title falls back to the request's intent_summary when no prose
    remains."""
    reply = """\
```bsnexus-verification
{"verifier_type": "software_test", "command": ["pytest"]}
```
"""
    run = await _seed_run_with_reply(db_session, mock_tenant_id, reply_text=reply)
    stream_manager = AsyncMock()
    stream_manager.publish = AsyncMock(return_value="0-0")

    await publish_run_output(run, db_session, stream_manager=stream_manager)
    await db_session.commit()

    from backend.src.models import ConversationMessage, Deliverable
    from sqlalchemy import select

    deliverable = (
        await db_session.execute(select(Deliverable).where(Deliverable.project_id == run.project_id))
    ).scalar_one()
    assert deliverable.title != "bsnexus-verification"
    # verifier_type is still stamped (parsed from raw reply).
    assert deliverable.verifier_type == "software_test"

    # And the chat message that mirrors the reply for the founder must
    # not show the JSON either.
    msg = (
        await db_session.execute(
            select(ConversationMessage)
            .where(ConversationMessage.project_id == run.project_id)
            .where(ConversationMessage.role == "assistant")
        )
    ).scalar_one_or_none()
    if msg is not None:
        assert "bsnexus-verification" not in msg.content
        assert "verifier_type" not in msg.content


@pytest.mark.asyncio
async def test_publish_run_output_ignores_malformed_block(db_session, mock_tenant_id, seeded_tenant) -> None:
    """Malformed fenced JSON ⇒ no-op, deliverable still created cleanly.
    The orchestrator must never propagate parser failures."""
    reply = """\
done.

```bsnexus-verification
{verifier_type: software_test
```
"""
    run = await _seed_run_with_reply(db_session, mock_tenant_id, reply_text=reply)
    stream_manager = AsyncMock()
    stream_manager.publish = AsyncMock(return_value="0-0")

    await publish_run_output(run, db_session, stream_manager=stream_manager)
    await db_session.commit()

    from backend.src.models import Deliverable
    from sqlalchemy import select

    deliverable = (
        await db_session.execute(select(Deliverable).where(Deliverable.project_id == run.project_id))
    ).scalar_one()
    assert deliverable.verifier_type is None
    stream_manager.publish.assert_not_awaited()
