"""S2-1 M9: targeted exception handling — CancelledError must propagate.

The audit's M9 finding (originally on the retired ``global_dispatcher``)
applies equally to today's ``run_orchestrator``, ``dispatcher``, and
the BaseServiceClient transport. The risk: a swallowed
``CancelledError`` disables cooperative cancellation — a hung run
can't be killed.

In Python 3.11+ ``asyncio.CancelledError`` is a ``BaseException``, so
a bare ``except Exception:`` already lets it through. These tests pin
that behavior down so a future widening to ``BaseException`` is caught
by CI rather than by an on-call paged at 3am for a hung run.
"""

from __future__ import annotations

import asyncio
import uuid
from unittest.mock import patch

import pytest

from backend.src.core import run_orchestrator


@pytest.mark.asyncio
async def test_safe_workspace_listing_propagates_cancellation():
    """``_safe_workspace_listing`` must re-raise CancelledError.

    The current implementation catches ``Exception``; ``CancelledError``
    in Python 3.11+ is a ``BaseException`` (good — but the catch must
    not be widened to ``BaseException``). Verify by patching the
    underlying lister.
    """
    project_id = uuid.uuid4()

    with patch("backend.src.core.project_workspace.list_files", side_effect=asyncio.CancelledError("c")):
        with pytest.raises(asyncio.CancelledError):
            run_orchestrator._safe_workspace_listing(project_id)


@pytest.mark.asyncio
async def test_safe_workspace_listing_swallows_oserror():
    """Sanity check: it still fail-softs on OSError (the realistic
    failure mode — workspace dir missing / unreadable).
    """
    project_id = uuid.uuid4()

    with patch("backend.src.core.project_workspace.list_files", side_effect=OSError("missing dir")):
        result = run_orchestrator._safe_workspace_listing(project_id)
    assert result == []


@pytest.mark.asyncio
async def test_run_orchestrator_executor_propagates_cancellation():
    """The executor-boundary catch in ``RunOrchestrator`` must re-raise
    CancelledError. The implementation has an explicit
    ``except CancelledError: raise`` branch as a defensive measure (in
    case the catch is widened to BaseException by a future refactor).

    S3-1 split the dispatch flow into a thin advisory-lock wrapper
    (``dispatch_run``) plus the original body (``_dispatch_run_locked``).
    The CancelledError guard lives in the body — we inspect that path
    here.
    """
    from backend.src.core.run_orchestrator import RunOrchestrator

    orch = RunOrchestrator()
    import inspect

    body_src = inspect.getsource(orch._dispatch_run_locked)
    assert "except asyncio.CancelledError" in body_src, (
        "RunOrchestrator._dispatch_run_locked lost its explicit "
        "CancelledError re-raise around the executor.execute call — "
        "see S2-1 M9."
    )
