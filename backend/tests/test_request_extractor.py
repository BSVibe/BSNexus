"""Tests for RequestExtractor + classifiers."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

import pytest

from backend.src.core.request_extractor import (
    ClassificationResult,
    MessageIntent,
    RequestExtractor,
    StaticKeywordClassifier,
)
from backend.src.models import Request, RequestStatus


@dataclass
class FakeClassifier:
    scripted: dict[str, ClassificationResult]

    async def classify(
        self, content: str, open_request_summaries: list[str]
    ) -> ClassificationResult:
        return self.scripted.get(
            content,
            ClassificationResult(MessageIntent.chit_chat, "", 0.5),
        )


class FakeDB:
    """Minimal in-memory AsyncSession stand-in.

    Supports ``add``, ``execute`` (for open-requests query), ``flush``.
    """

    def __init__(self) -> None:
        self.added: list[Any] = []
        self.open_requests: list[Request] = []

    def add(self, obj: Any) -> None:
        self.added.append(obj)
        if isinstance(obj, Request):
            if not hasattr(obj, "id") or obj.id is None:
                obj.id = uuid.uuid4()

    async def flush(self) -> None:
        for obj in self.added:
            if isinstance(obj, Request) and getattr(obj, "id", None) is None:
                obj.id = uuid.uuid4()

    async def execute(self, stmt: Any) -> Any:
        async def scalars():
            return self.open_requests

        return SimpleNamespace(scalars=lambda: self.open_requests)


def _fake_message(role: str = "user", content: str = ""):
    return SimpleNamespace(
        id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        role=role,
        content=content,
        request_id=None,
    )


@pytest.mark.asyncio
async def test_static_classifier_rejects_chit_chat():
    c = StaticKeywordClassifier()
    result = await c.classify("hi there!", [])
    assert result.intent == MessageIntent.chit_chat


@pytest.mark.asyncio
async def test_static_classifier_detects_request():
    c = StaticKeywordClassifier()
    result = await c.classify("Please implement login", [])
    assert result.intent == MessageIntent.request
    assert "implement login" in result.intent_summary.lower()


@pytest.mark.asyncio
async def test_static_classifier_detects_question():
    c = StaticKeywordClassifier()
    result = await c.classify("How does auth work here?", [])
    assert result.intent == MessageIntent.question


@pytest.mark.asyncio
async def test_extractor_no_op_for_assistant_messages():
    ex = RequestExtractor(FakeClassifier(scripted={}))
    db = FakeDB()
    msg = _fake_message(role="assistant", content="Done.")
    outcome = await ex.process_message(msg, tenant_id=uuid.uuid4(), db=db)
    assert outcome.request is None
    assert outcome.created_new is False
    assert db.added == []


@pytest.mark.asyncio
async def test_extractor_skips_chit_chat():
    ex = RequestExtractor(
        FakeClassifier(
            scripted={
                "hi": ClassificationResult(MessageIntent.chit_chat, "", 1.0),
            }
        )
    )
    db = FakeDB()
    msg = _fake_message(content="hi")
    outcome = await ex.process_message(msg, tenant_id=uuid.uuid4(), db=db)
    assert outcome.request is None
    assert db.added == []
    assert msg.request_id is None


@pytest.mark.asyncio
async def test_extractor_creates_request_on_new_intent():
    tenant_id = uuid.uuid4()
    ex = RequestExtractor(
        FakeClassifier(
            scripted={
                "Ship the landing page": ClassificationResult(
                    MessageIntent.request, "ship landing page", 0.9
                ),
            }
        )
    )
    db = FakeDB()
    msg = _fake_message(content="Ship the landing page")

    outcome = await ex.process_message(msg, tenant_id=tenant_id, db=db)

    assert outcome.intent == MessageIntent.request
    assert outcome.created_new is True
    assert outcome.request is not None
    assert outcome.request.tenant_id == tenant_id
    assert outcome.request.intent_summary == "ship landing page"
    assert msg.request_id == outcome.request.id
    assert any(isinstance(o, Request) for o in db.added)


@pytest.mark.asyncio
async def test_extractor_appends_modification_to_open_request():
    tenant_id = uuid.uuid4()
    project_id = uuid.uuid4()
    existing = Request(
        tenant_id=tenant_id,
        project_id=project_id,
        intent_summary="ship landing page",
        status=RequestStatus.open,
    )
    existing.id = uuid.uuid4()
    db = FakeDB()
    db.open_requests = [existing]

    ex = RequestExtractor(
        FakeClassifier(
            scripted={
                "Also add a testimonial section": ClassificationResult(
                    MessageIntent.modification, "add testimonial section", 0.8
                ),
            }
        )
    )
    msg = _fake_message(content="Also add a testimonial section")
    msg.project_id = project_id

    outcome = await ex.process_message(msg, tenant_id=tenant_id, db=db)

    assert outcome.intent == MessageIntent.modification
    assert outcome.created_new is False
    assert outcome.request is existing
    assert msg.request_id == existing.id


@pytest.mark.asyncio
async def test_extractor_modification_with_no_open_requests_creates_new():
    tenant_id = uuid.uuid4()
    ex = RequestExtractor(
        FakeClassifier(
            scripted={
                "Remove the footer": ClassificationResult(
                    MessageIntent.modification, "remove footer", 0.7
                ),
            }
        )
    )
    db = FakeDB()
    msg = _fake_message(content="Remove the footer")

    outcome = await ex.process_message(msg, tenant_id=tenant_id, db=db)

    assert outcome.intent == MessageIntent.modification
    assert outcome.created_new is True
    assert outcome.request is not None
