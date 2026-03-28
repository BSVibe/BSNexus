"""Tests for provider dependency injection."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from backend.src.providers.dependencies import (
    create_gateway_provider,
    create_knowledge_provider,
    create_supervisor_provider,
    get_gateway_provider,
    get_knowledge_provider,
    get_supervisor_provider,
)
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
from backend.src.providers.supervisor import (
    BSupervisorProvider,
    NoOpSupervisorProvider,
    SupervisorProvider,
)


class TestCreateGatewayProvider:
    """Tests for create_gateway_provider factory."""

    def test_creates_litellm_by_default(self) -> None:
        with patch("backend.src.providers.dependencies.settings") as mock_settings:
            mock_settings.gateway_provider = "litellm"
            mock_settings.default_llm_model = "gpt-4o"
            mock_settings.default_llm_base_url = None

            provider = create_gateway_provider()

            assert isinstance(provider, LiteLLMDirectProvider)
            assert isinstance(provider, GatewayProvider)

    def test_creates_bsgateway_when_configured(self) -> None:
        with patch("backend.src.providers.dependencies.settings") as mock_settings:
            mock_settings.gateway_provider = "bsgateway"
            mock_settings.bsgateway_url = "https://gateway.example.com"
            mock_settings.bsgateway_api_key = "test-key"

            provider = create_gateway_provider()

            assert isinstance(provider, BSGatewayProvider)
            assert isinstance(provider, GatewayProvider)

    def test_falls_back_to_litellm_for_unknown(self) -> None:
        with patch("backend.src.providers.dependencies.settings") as mock_settings:
            mock_settings.gateway_provider = "litellm"
            mock_settings.default_llm_model = "gpt-4o"
            mock_settings.default_llm_base_url = None

            provider = create_gateway_provider()

            assert isinstance(provider, LiteLLMDirectProvider)


class TestCreateSupervisorProvider:
    """Tests for create_supervisor_provider factory."""

    def test_creates_noop_by_default(self) -> None:
        with patch("backend.src.providers.dependencies.settings") as mock_settings:
            mock_settings.supervisor_provider = "noop"

            provider = create_supervisor_provider()

            assert isinstance(provider, NoOpSupervisorProvider)
            assert isinstance(provider, SupervisorProvider)

    def test_creates_bsupervisor_when_configured(self) -> None:
        with patch("backend.src.providers.dependencies.settings") as mock_settings:
            mock_settings.supervisor_provider = "bsupervisor"
            mock_settings.bsupervisor_url = "https://supervisor.example.com"
            mock_settings.bsupervisor_api_key = "test-key"

            provider = create_supervisor_provider()

            assert isinstance(provider, BSupervisorProvider)
            assert isinstance(provider, SupervisorProvider)


class TestCreateKnowledgeProvider:
    """Tests for create_knowledge_provider factory."""

    def test_creates_local_by_default(self) -> None:
        with patch("backend.src.providers.dependencies.settings") as mock_settings:
            mock_settings.knowledge_provider = "local"
            mock_settings.knowledge_dir = "./knowledge"

            provider = create_knowledge_provider()

            assert isinstance(provider, LocalMarkdownProvider)
            assert isinstance(provider, KnowledgeProvider)

    def test_creates_bsage_when_configured(self) -> None:
        with patch("backend.src.providers.dependencies.settings") as mock_settings:
            mock_settings.knowledge_provider = "bsage"
            mock_settings.bsage_url = "https://sage.example.com"
            mock_settings.bsage_api_key = "test-key"

            provider = create_knowledge_provider()

            assert isinstance(provider, BSageProvider)
            assert isinstance(provider, KnowledgeProvider)


class TestDependencyFunctions:
    """Tests for FastAPI Depends()-compatible functions."""

    def test_get_gateway_provider_returns_cached_instance(self) -> None:
        """Dependency function returns provider (singleton pattern)."""
        with patch("backend.src.providers.dependencies.settings") as mock_settings:
            mock_settings.gateway_provider = "litellm"
            mock_settings.default_llm_model = "gpt-4o"
            mock_settings.default_llm_base_url = None

            provider = get_gateway_provider()

            assert isinstance(provider, GatewayProvider)

    def test_get_supervisor_provider_returns_instance(self) -> None:
        with patch("backend.src.providers.dependencies.settings") as mock_settings:
            mock_settings.supervisor_provider = "noop"

            provider = get_supervisor_provider()

            assert isinstance(provider, SupervisorProvider)

    def test_get_knowledge_provider_returns_instance(self) -> None:
        with patch("backend.src.providers.dependencies.settings") as mock_settings:
            mock_settings.knowledge_provider = "local"
            mock_settings.knowledge_dir = "./knowledge"

            provider = get_knowledge_provider()

            assert isinstance(provider, KnowledgeProvider)


class TestProviderOverrideInTests:
    """Tests that providers can be overridden in FastAPI test client."""

    async def test_gateway_provider_override(self) -> None:
        from backend.src.main import app

        mock_provider = AsyncMock(spec=GatewayProvider)
        app.dependency_overrides[get_gateway_provider] = lambda: mock_provider

        try:
            result = app.dependency_overrides[get_gateway_provider]()
            assert result is mock_provider
        finally:
            app.dependency_overrides.pop(get_gateway_provider, None)

    async def test_supervisor_provider_override(self) -> None:
        from backend.src.main import app

        mock_provider = AsyncMock(spec=SupervisorProvider)
        app.dependency_overrides[get_supervisor_provider] = lambda: mock_provider

        try:
            result = app.dependency_overrides[get_supervisor_provider]()
            assert result is mock_provider
        finally:
            app.dependency_overrides.pop(get_supervisor_provider, None)

    async def test_knowledge_provider_override(self) -> None:
        from backend.src.main import app

        mock_provider = AsyncMock(spec=KnowledgeProvider)
        app.dependency_overrides[get_knowledge_provider] = lambda: mock_provider

        try:
            result = app.dependency_overrides[get_knowledge_provider]()
            assert result is mock_provider
        finally:
            app.dependency_overrides.pop(get_knowledge_provider, None)
