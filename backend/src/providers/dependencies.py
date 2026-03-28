"""Provider factory functions for FastAPI dependency injection.

Self-contained module — imports provider modules individually to avoid
cross-provider coupling. Each factory reads settings to resolve the
configured implementation.
"""

from __future__ import annotations

import functools

import structlog

from backend.src.config import settings
from backend.src.providers.gateway import (
    BSGatewayProvider,
    GatewayProvider,
    LiteLLMDirectProvider,
)
from backend.src.providers.knowledge import (
    BSageProvider,
    KnowledgeProvider,
    LocalMarkdownProvider,
)
from backend.src.providers.notification import (
    BSageNotificationProvider,
    NoOpNotificationProvider,
    NotificationProvider,
)
from backend.src.providers.supervisor import (
    BSupervisorProvider,
    NoOpSupervisorProvider,
    SupervisorProvider,
)

logger = structlog.get_logger(__name__)


def create_gateway_provider() -> GatewayProvider:
    """Create a GatewayProvider based on current settings."""
    if settings.gateway_provider == "bsgateway":
        logger.info("provider_init", provider="bsgateway", url=settings.bsgateway_url)
        return BSGatewayProvider(
            base_url=settings.bsgateway_url,
            api_key=settings.bsgateway_api_key,
        )

    logger.info("provider_init", provider="litellm_direct")
    return LiteLLMDirectProvider(
        default_model=settings.default_llm_model,
        base_url=settings.default_llm_base_url,
    )


def create_supervisor_provider() -> SupervisorProvider:
    """Create a SupervisorProvider based on current settings."""
    if settings.supervisor_provider == "bsupervisor":
        logger.info("provider_init", provider="bsupervisor", url=settings.bsupervisor_url)
        return BSupervisorProvider(
            base_url=settings.bsupervisor_url,
            api_key=settings.bsupervisor_api_key,
        )

    logger.info("provider_init", provider="noop_supervisor")
    return NoOpSupervisorProvider()


def create_knowledge_provider() -> KnowledgeProvider:
    """Create a KnowledgeProvider based on current settings."""
    if settings.knowledge_provider == "bsage":
        logger.info("provider_init", provider="bsage", url=settings.bsage_url)
        return BSageProvider(
            base_url=settings.bsage_url,
            api_key=settings.bsage_api_key,
        )

    logger.info("provider_init", provider="local_markdown", knowledge_dir=settings.knowledge_dir)
    return LocalMarkdownProvider(knowledge_dir=settings.knowledge_dir)


def create_notification_provider() -> NotificationProvider:
    """Create a NotificationProvider based on current settings."""
    if settings.notification_provider == "bsage":
        base_url = settings.bsage_notification_url or settings.bsage_url
        logger.info("provider_init", provider="bsage_notification", url=base_url)
        return BSageNotificationProvider(
            base_url=base_url,
            api_key=settings.bsage_api_key,
        )

    logger.info("provider_init", provider="noop_notification")
    return NoOpNotificationProvider()


@functools.lru_cache(maxsize=1)
def get_gateway_provider() -> GatewayProvider:
    """FastAPI dependency — returns a cached GatewayProvider singleton."""
    return create_gateway_provider()


@functools.lru_cache(maxsize=1)
def get_supervisor_provider() -> SupervisorProvider:
    """FastAPI dependency — returns a cached SupervisorProvider singleton."""
    return create_supervisor_provider()


@functools.lru_cache(maxsize=1)
def get_knowledge_provider() -> KnowledgeProvider:
    """FastAPI dependency — returns a cached KnowledgeProvider singleton."""
    return create_knowledge_provider()


@functools.lru_cache(maxsize=1)
def get_notification_provider() -> NotificationProvider:
    """FastAPI dependency — returns a cached NotificationProvider singleton."""
    return create_notification_provider()
