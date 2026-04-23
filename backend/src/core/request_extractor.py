"""RequestExtractor — converts user chat messages into Requests.

Every ``conversation_messages`` row with ``role="user"`` is classified
into one of four intents:

- ``chit_chat``: small talk; no Request side effect.
- ``question``: asked for info; no Request side effect.
- ``request``: new unit of work.
- ``modification``: amends an existing open Request.

The classifier is pluggable. The default calls a cheap LiteLLM model
(BSGateway will route it through its hook). Tests inject a fake.
"""

from __future__ import annotations

import enum
import uuid
from dataclasses import dataclass
from typing import Protocol

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.src.models import ConversationMessage, Request, RequestStatus

logger = structlog.get_logger(__name__)


class MessageIntent(str, enum.Enum):
    chit_chat = "chit_chat"
    question = "question"
    request = "request"
    modification = "modification"


@dataclass(frozen=True)
class ClassificationResult:
    intent: MessageIntent
    intent_summary: str  # concise rephrase for Request.intent_summary
    confidence: float


class Classifier(Protocol):
    async def classify(
        self, content: str, open_request_summaries: list[str]
    ) -> ClassificationResult: ...


class StaticKeywordClassifier:
    """Fallback classifier used when no LLM is available.

    Heuristic:
    - Empty → ``chit_chat``.
    - Ends with "?" without request verbs → ``question``.
    - Contains a modification verb (change/update/also/actually/but…)
      AND there are open requests → ``modification``.
    - Contains a request verb → ``request``.
    - Otherwise ``chit_chat``.
    """

    REQUEST_KEYWORDS = {
        "implement", "build", "add", "create", "fix", "refactor",
        "write", "design", "update", "change", "remove", "delete",
        "ship", "please",
        # Korean — matched as substrings so ``만들어줘`` / ``만들어주세요``
        # / ``작성해 줘`` all hit. ``주세요`` is the canonical polite
        # imperative suffix — covers most directive Korean messages that
        # don't contain a more specific verb.
        "만들", "작성", "디자인", "구현", "개발", "설계", "고쳐",
        "추가해", "리팩토", "부탁", "제작", "주세요", "해줘",
        "분리", "출력", "생성",
    }

    MODIFICATION_CUES = {
        "change", "update", "also", "instead", "actually",
        "but ", "rather", "revise", "reword", "reconsider",
        # Korean
        "바꿔", "수정", "변경", "대신", "다시", "또한",
    }

    async def classify(
        self, content: str, open_request_summaries: list[str]
    ) -> ClassificationResult:
        lowered = content.strip().lower()
        if not lowered:
            return ClassificationResult(MessageIntent.chit_chat, "", 1.0)

        if lowered.endswith("?") and not any(
            k in lowered for k in self.REQUEST_KEYWORDS
        ):
            return ClassificationResult(MessageIntent.question, lowered[:120], 0.7)

        has_request_cue = any(k in lowered for k in self.REQUEST_KEYWORDS)
        has_modification_cue = any(cue in lowered for cue in self.MODIFICATION_CUES)

        if open_request_summaries and has_modification_cue:
            return ClassificationResult(
                MessageIntent.modification, content.strip()[:240], 0.65
            )

        if has_request_cue:
            return ClassificationResult(
                MessageIntent.request, content.strip()[:240], 0.6
            )

        return ClassificationResult(MessageIntent.chit_chat, "", 0.5)


class LiteLLMClassifier:
    """Classifier backed by a cheap LLM via LiteLLM (BSGateway-routed).

    Kept thin: caller wires up the model id and base URL.
    """

    SYSTEM_PROMPT = (
        "Classify the user message into exactly one of: chit_chat, question, "
        "request, modification. If 'request' or 'modification', also produce "
        "a concise intent summary (max 200 chars). "
        "Respond as JSON: "
        '{"intent":"...","intent_summary":"...","confidence":0.0}'
    )

    def __init__(self, model: str, *, base_url: str | None = None, api_key: str | None = None):
        self._model = model
        self._base_url = base_url
        self._api_key = api_key

    async def classify(
        self, content: str, open_request_summaries: list[str]
    ) -> ClassificationResult:
        import json as _json

        import litellm  # imported lazily so tests that don't exercise LiteLLM don't require it

        existing = (
            "Open requests on this project:\n- "
            + "\n- ".join(open_request_summaries)
            if open_request_summaries
            else "No open requests on this project."
        )
        user_content = f"{existing}\n\nNew user message:\n{content}"

        try:
            resp = await litellm.acompletion(
                model=self._model,
                api_key=self._api_key,
                base_url=self._base_url,
                messages=[
                    {"role": "system", "content": self.SYSTEM_PROMPT},
                    {"role": "user", "content": user_content},
                ],
                temperature=0.0,
                max_tokens=200,
            )
            raw = resp["choices"][0]["message"]["content"]  # type: ignore[index]
            data = _json.loads(raw)
            return ClassificationResult(
                intent=MessageIntent(data.get("intent", "chit_chat")),
                intent_summary=data.get("intent_summary", "")[:240],
                confidence=float(data.get("confidence", 0.5)),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("classifier_failed_fallback_static", error=str(exc))
            return await StaticKeywordClassifier().classify(
                content, open_request_summaries
            )


@dataclass(frozen=True)
class ExtractionOutcome:
    """What happened to the message."""

    intent: MessageIntent
    request: Request | None
    created_new: bool  # True = new Request row. False = appended or no-op.


class RequestExtractor:
    """Processes a conversation message → optional Request side effect.

    Callers pass ``tenant_id`` explicitly because ``conversation_messages``
    doesn't carry it (scoping is via ``projects``). The chat endpoint
    already has it from the ``TenantMiddleware``.
    """

    def __init__(self, classifier: Classifier | None = None):
        self._classifier = classifier or StaticKeywordClassifier()

    async def process_message(
        self,
        message: ConversationMessage,
        *,
        tenant_id: uuid.UUID,
        db: AsyncSession,
    ) -> ExtractionOutcome:
        if message.role != "user":
            return ExtractionOutcome(MessageIntent.chit_chat, None, False)

        open_requests = await _load_open_requests(db, message.project_id)
        summaries = [r.intent_summary for r in open_requests]

        classification = await self._classifier.classify(message.content, summaries)

        if classification.intent in (MessageIntent.chit_chat, MessageIntent.question):
            return ExtractionOutcome(classification.intent, None, False)

        if classification.intent == MessageIntent.modification and open_requests:
            request = _pick_best_match(classification.intent_summary, open_requests)
            if request is not None:
                message.request_id = request.id
                await db.flush()
                logger.info(
                    "request_extended",
                    request_id=str(request.id),
                    message_id=str(message.id),
                )
                return ExtractionOutcome(classification.intent, request, False)

        request = Request(
            tenant_id=tenant_id,
            project_id=message.project_id,
            origin_message_id=message.id,
            intent_summary=classification.intent_summary or message.content[:240],
            status=RequestStatus.open,
        )
        db.add(request)
        await db.flush()
        message.request_id = request.id
        logger.info(
            "request_created",
            request_id=str(request.id),
            intent=classification.intent.value,
        )
        return ExtractionOutcome(classification.intent, request, True)


async def _load_open_requests(
    db: AsyncSession, project_id: uuid.UUID
) -> list[Request]:
    stmt = (
        select(Request)
        .where(
            Request.project_id == project_id,
            Request.status.in_([RequestStatus.open, RequestStatus.running]),
        )
        .order_by(Request.created_at.desc())
    )
    result = await db.execute(stmt)
    return list(result.scalars())


def _pick_best_match(
    intent_summary: str, requests: list[Request]
) -> Request | None:
    """Trivial match: first open request if we have one.

    v1 strategy — keep simple; let the UI expose split/merge controls
    so the user can correct mismatches.
    """
    return requests[0] if requests else None


