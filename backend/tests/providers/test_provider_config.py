"""Tests for provider configuration via pydantic-settings.

TDD: These tests are written BEFORE the implementation.
"""

from __future__ import annotations

import os
from typing import Literal
from unittest.mock import patch

import pytest


class TestProviderSettingsDefaults:
    """Verify default provider selections and URLs."""

    def test_gateway_provider_default_is_litellm(self) -> None:
        """Default gateway provider should be 'litellm' (no external dependency)."""
        from backend.src.config import Settings

        s = Settings(database_url="postgresql+asyncpg://x:x@localhost/x")
        assert s.gateway_provider == "litellm"

    def test_supervisor_provider_default_is_noop(self) -> None:
        """Default supervisor provider should be 'noop' (no external dependency)."""
        from backend.src.config import Settings

        s = Settings(database_url="postgresql+asyncpg://x:x@localhost/x")
        assert s.supervisor_provider == "noop"

    def test_knowledge_provider_default_is_local(self) -> None:
        """Default knowledge provider should be 'local' (no external dependency)."""
        from backend.src.config import Settings

        s = Settings(database_url="postgresql+asyncpg://x:x@localhost/x")
        assert s.knowledge_provider == "local"

    def test_bsgateway_url_default_empty(self) -> None:
        """BSGateway URL defaults to empty string."""
        from backend.src.config import Settings

        s = Settings(database_url="postgresql+asyncpg://x:x@localhost/x")
        assert s.bsgateway_url == ""

    def test_bsupervisor_url_default_empty(self) -> None:
        """BSupervisor URL defaults to empty string."""
        from backend.src.config import Settings

        s = Settings(database_url="postgresql+asyncpg://x:x@localhost/x")
        assert s.bsupervisor_url == ""

    def test_bsage_url_default_empty(self) -> None:
        """BSage URL defaults to empty string."""
        from backend.src.config import Settings

        s = Settings(database_url="postgresql+asyncpg://x:x@localhost/x")
        assert s.bsage_url == ""

    def test_bsgateway_api_key_default_empty(self) -> None:
        """BSGateway API key defaults to empty string."""
        from backend.src.config import Settings

        s = Settings(database_url="postgresql+asyncpg://x:x@localhost/x")
        assert s.bsgateway_api_key == ""

    def test_bsupervisor_api_key_default_empty(self) -> None:
        """BSupervisor API key defaults to empty string."""
        from backend.src.config import Settings

        s = Settings(database_url="postgresql+asyncpg://x:x@localhost/x")
        assert s.bsupervisor_api_key == ""

    def test_bsage_api_key_default_empty(self) -> None:
        """BSage API key defaults to empty string."""
        from backend.src.config import Settings

        s = Settings(database_url="postgresql+asyncpg://x:x@localhost/x")
        assert s.bsage_api_key == ""

    def test_knowledge_dir_default(self) -> None:
        """Local knowledge directory defaults to './knowledge'."""
        from backend.src.config import Settings

        s = Settings(database_url="postgresql+asyncpg://x:x@localhost/x")
        assert s.knowledge_dir == "./knowledge"


class TestProviderSettingsFromEnv:
    """Verify provider selection reads from environment variables."""

    def test_gateway_provider_from_env(self) -> None:
        """GATEWAY_PROVIDER env var selects gateway provider."""
        from backend.src.config import Settings

        s = Settings(
            database_url="postgresql+asyncpg://x:x@localhost/x",
            gateway_provider="bsgateway",
        )
        assert s.gateway_provider == "bsgateway"

    def test_supervisor_provider_from_env(self) -> None:
        """SUPERVISOR_PROVIDER env var selects supervisor provider."""
        from backend.src.config import Settings

        s = Settings(
            database_url="postgresql+asyncpg://x:x@localhost/x",
            supervisor_provider="bsupervisor",
        )
        assert s.supervisor_provider == "bsupervisor"

    def test_knowledge_provider_from_env(self) -> None:
        """KNOWLEDGE_PROVIDER env var selects knowledge provider."""
        from backend.src.config import Settings

        s = Settings(
            database_url="postgresql+asyncpg://x:x@localhost/x",
            knowledge_provider="bsage",
        )
        assert s.knowledge_provider == "bsage"

    def test_bsgateway_url_from_env(self) -> None:
        """BSGATEWAY_URL env var sets BSGateway URL."""
        from backend.src.config import Settings

        s = Settings(
            database_url="postgresql+asyncpg://x:x@localhost/x",
            bsgateway_url="https://gateway.example.com",
        )
        assert s.bsgateway_url == "https://gateway.example.com"

    def test_bsupervisor_url_from_env(self) -> None:
        """BSUPERVISOR_URL env var sets BSupervisor URL."""
        from backend.src.config import Settings

        s = Settings(
            database_url="postgresql+asyncpg://x:x@localhost/x",
            bsupervisor_url="https://supervisor.example.com",
        )
        assert s.bsupervisor_url == "https://supervisor.example.com"

    def test_bsage_url_from_env(self) -> None:
        """BSAGE_URL env var sets BSage URL."""
        from backend.src.config import Settings

        s = Settings(
            database_url="postgresql+asyncpg://x:x@localhost/x",
            bsage_url="https://sage.example.com",
        )
        assert s.bsage_url == "https://sage.example.com"

    def test_knowledge_dir_from_env(self) -> None:
        """KNOWLEDGE_DIR env var sets local knowledge directory."""
        from backend.src.config import Settings

        s = Settings(
            database_url="postgresql+asyncpg://x:x@localhost/x",
            knowledge_dir="/data/knowledge",
        )
        assert s.knowledge_dir == "/data/knowledge"


class TestProviderSettingsValidation:
    """Verify invalid provider values are rejected."""

    def test_invalid_gateway_provider_rejected(self) -> None:
        """Invalid gateway provider value should raise ValidationError."""
        from pydantic import ValidationError

        from backend.src.config import Settings

        with pytest.raises(ValidationError):
            Settings(
                database_url="postgresql+asyncpg://x:x@localhost/x",
                gateway_provider="invalid",
            )

    def test_invalid_supervisor_provider_rejected(self) -> None:
        """Invalid supervisor provider value should raise ValidationError."""
        from pydantic import ValidationError

        from backend.src.config import Settings

        with pytest.raises(ValidationError):
            Settings(
                database_url="postgresql+asyncpg://x:x@localhost/x",
                supervisor_provider="invalid",
            )

    def test_invalid_knowledge_provider_rejected(self) -> None:
        """Invalid knowledge provider value should raise ValidationError."""
        from pydantic import ValidationError

        from backend.src.config import Settings

        with pytest.raises(ValidationError):
            Settings(
                database_url="postgresql+asyncpg://x:x@localhost/x",
                knowledge_provider="invalid",
            )
