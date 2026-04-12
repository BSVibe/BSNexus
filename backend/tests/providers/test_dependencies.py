"""Tests for provider dependency injection."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch


from backend.src.providers.dependencies import (
    create_gateway_provider,
    create_knowledge_provider,
    get_gateway_provider,
    get_knowledge_provider,
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
        with patch("backend.src.providers.dependencies.settings") as mock_settings:
            mock_settings.gateway_provider = "litellm"
            mock_settings.default_llm_model = "gpt-4o"
            mock_settings.default_llm_base_url = None

            provider = get_gateway_provider()

            assert isinstance(provider, GatewayProvider)

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

    async def test_knowledge_provider_override(self) -> None:
        from backend.src.main import app

        mock_provider = AsyncMock(spec=KnowledgeProvider)
        app.dependency_overrides[get_knowledge_provider] = lambda: mock_provider

        try:
            result = app.dependency_overrides[get_knowledge_provider]()
            assert result is mock_provider
        finally:
            app.dependency_overrides.pop(get_knowledge_provider, None)
