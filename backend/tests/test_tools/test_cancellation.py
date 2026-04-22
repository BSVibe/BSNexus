"""Tests for tools/cancellation.py — CancellationToken."""

from __future__ import annotations

import uuid

import pytest

from backend.src.tools.cancellation import CancellationToken


@pytest.fixture(autouse=True)
def _clean_cancellation_state():
    """Ensure clean state between tests."""
    yield
    CancellationToken._cancelled.clear()


class TestCancellationToken:
    def test_initially_not_cancelled(self) -> None:
        pid = uuid.uuid4()
        assert not CancellationToken.is_cancelled(pid)

    def test_cancel_sets_flag(self) -> None:
        pid = uuid.uuid4()
        CancellationToken.cancel(pid)
        assert CancellationToken.is_cancelled(pid)

    def test_reset_clears_flag(self) -> None:
        pid = uuid.uuid4()
        CancellationToken.cancel(pid)
        CancellationToken.reset(pid)
        assert not CancellationToken.is_cancelled(pid)

    def test_cleanup_removes_state(self) -> None:
        pid = uuid.uuid4()
        CancellationToken.cancel(pid)
        CancellationToken.cleanup(pid)
        assert not CancellationToken.is_cancelled(pid)

    def test_independent_projects(self) -> None:
        pid1 = uuid.uuid4()
        pid2 = uuid.uuid4()
        CancellationToken.cancel(pid1)
        assert CancellationToken.is_cancelled(pid1)
        assert not CancellationToken.is_cancelled(pid2)
