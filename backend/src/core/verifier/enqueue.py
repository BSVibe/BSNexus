"""Enqueue helpers for the verification queue."""

from __future__ import annotations

import structlog

from backend.src.core.verifier.protocol import VerificationEnvelope, VerifierType
from backend.src.models import Deliverable
from backend.src.queue.streams import RedisStreamManager

logger = structlog.get_logger(__name__)

VERIFICATION_QUEUE_STREAM = "verification:queue"
VERIFICATION_CONSUMER_GROUP = "verifier"


async def enqueue_verification(
    stream_manager: RedisStreamManager,
    envelope: VerificationEnvelope,
) -> str:
    """Publish ``envelope`` onto the verification queue and return the
    Redis Stream message id."""
    message_id = await stream_manager.publish(
        VERIFICATION_QUEUE_STREAM,
        envelope.to_payload(),
    )
    logger.info(
        "verification_enqueued",
        deliverable_id=str(envelope.deliverable_id),
        verifier_type=envelope.verifier_type.value,
        attempt=envelope.attempt,
        message_id=message_id,
    )
    return message_id


async def maybe_enqueue_for_deliverable(
    stream_manager: RedisStreamManager | None,
    deliverable: Deliverable,
) -> str | None:
    """Enqueue verification for ``deliverable`` if it carries the
    information a verifier needs.

    No-op when:
    - ``stream_manager`` is None (test mode / verifier subsystem off)
    - ``deliverable.verifier_type`` is unset (the LLM didn't propose one)
    - ``verifier_type`` doesn't match a known :class:`VerifierType`

    Returns the message id when an envelope was enqueued.
    """
    if stream_manager is None:
        return None
    if not deliverable.verifier_type:
        return None
    try:
        verifier_type = VerifierType(deliverable.verifier_type)
    except ValueError:
        logger.warning(
            "verifier_unknown_type_on_deliverable",
            deliverable_id=str(deliverable.id),
            verifier_type=deliverable.verifier_type,
        )
        return None

    envelope = VerificationEnvelope(
        deliverable_id=deliverable.id,
        tenant_id=deliverable.tenant_id,
        project_id=deliverable.project_id,
        verifier_type=verifier_type,
        inputs=dict(deliverable.verifier_inputs or {}),
    )
    return await enqueue_verification(stream_manager, envelope)
