"""Tests for provider configuration via pydantic-settings."""

from __future__ import annotations

import pytest


class TestProviderSettingsDefaults:
    """Verify default provider selections and URLs."""

    def test_gateway_provider_default_is_litellm(self) -> None:
        from backend.src.config import Settings
        s = Settings(database_url="postgresql+asyncpg://x:x@localhost/x")
        assert s.gateway_provider == "litellm"

    def test_knowledge_provider_default_is_local(self) -> None:
        from backend.src.config import Settings
        s = Settings(database_url="postgresql+asyncpg://x:x@localhost/x")
        assert s.knowledge_provider == "local"

    def test_bsgateway_url_default_empty(self) -> None:
        from backend.src.config import Settings
        s = Settings(database_url="postgresql+asyncpg://x:x@localhost/x")
        assert s.bsgateway_url == ""

    def test_bsage_url_default_empty(self) -> None:
        from backend.src.config import Settings
        s = Settings(database_url="postgresql+asyncpg://x:x@localhost/x")
        assert s.bsage_url == ""

    def test_bsgateway_api_key_default_empty(self) -> None:
        from backend.src.config import Settings
        s = Settings(database_url="postgresql+asyncpg://x:x@localhost/x")
        assert s.bsgateway_api_key == ""

    def test_bsage_api_key_default_empty(self) -> None:
        from backend.src.config import Settings
        s = Settings(database_url="postgresql+asyncpg://x:x@localhost/x")
        assert s.bsage_api_key == ""

    def test_knowledge_dir_default(self) -> None:
        from backend.src.config import Settings
        s = Settings(database_url="postgresql+asyncpg://x:x@localhost/x")
        assert s.knowledge_dir == "./knowledge"


class TestProviderSettingsFromEnv:
    """Verify provider selection reads from environment variables."""

    def test_gateway_provider_from_env(self) -> None:
        from backend.src.config import Settings
        s = Settings(
            database_url="postgresql+asyncpg://x:x@localhost/x",
            gateway_provider="bsgateway",
        )
        assert s.gateway_provider == "bsgateway"

    def test_knowledge_provider_from_env(self) -> None:
        from backend.src.config import Settings
        s = Settings(
            database_url="postgresql+asyncpg://x:x@localhost/x",
            knowledge_provider="bsage",
        )
        assert s.knowledge_provider == "bsage"

    def test_bsgateway_url_from_env(self) -> None:
        from backend.src.config import Settings
        s = Settings(
            database_url="postgresql+asyncpg://x:x@localhost/x",
            bsgateway_url="https://gateway.example.com",
        )
        assert s.bsgateway_url == "https://gateway.example.com"

    def test_bsage_url_from_env(self) -> None:
        from backend.src.config import Settings
        s = Settings(
            database_url="postgresql+asyncpg://x:x@localhost/x",
            bsage_url="https://sage.example.com",
        )
        assert s.bsage_url == "https://sage.example.com"

    def test_knowledge_dir_from_env(self) -> None:
        from backend.src.config import Settings
        s = Settings(
            database_url="postgresql+asyncpg://x:x@localhost/x",
            knowledge_dir="/data/knowledge",
        )
        assert s.knowledge_dir == "/data/knowledge"


class TestProviderSettingsValidation:
    """Verify invalid provider values are rejected."""

    def test_invalid_gateway_provider_rejected(self) -> None:
        from pydantic import ValidationError
        from backend.src.config import Settings

        with pytest.raises(ValidationError):
            Settings(
                database_url="postgresql+asyncpg://x:x@localhost/x",
                gateway_provider="invalid",
            )

    def test_invalid_knowledge_provider_rejected(self) -> None:
        from pydantic import ValidationError
        from backend.src.config import Settings

        with pytest.raises(ValidationError):
            Settings(
                database_url="postgresql+asyncpg://x:x@localhost/x",
                knowledge_provider="invalid",
            )
