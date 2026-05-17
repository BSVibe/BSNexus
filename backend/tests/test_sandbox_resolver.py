"""Part B / PR 5 — sandbox manager resolver.

``sandbox_enabled`` decides DockerSandboxManager vs ``None`` (no
sandbox — host paths run unchanged).
"""

from __future__ import annotations

from backend.src.core.sandbox import DockerSandboxManager
from backend.src.core.sandbox import resolver as resolver_mod


def test_build_returns_none_when_sandbox_disabled(monkeypatch) -> None:
    monkeypatch.setattr(resolver_mod.settings, "sandbox_enabled", False)
    assert resolver_mod.build_sandbox_manager() is None


def test_build_returns_docker_when_sandbox_enabled(monkeypatch) -> None:
    monkeypatch.setattr(resolver_mod.settings, "sandbox_enabled", True)
    monkeypatch.setattr(resolver_mod.settings, "docker_host", "tcp://dind:2375")
    mgr = resolver_mod.build_sandbox_manager()
    assert isinstance(mgr, DockerSandboxManager)


def test_get_sandbox_manager_is_cached(monkeypatch) -> None:
    monkeypatch.setattr(resolver_mod.settings, "sandbox_enabled", True)
    monkeypatch.setattr(resolver_mod.settings, "docker_host", "tcp://dind:2375")
    resolver_mod.reset_sandbox_manager()
    first = resolver_mod.get_sandbox_manager()
    second = resolver_mod.get_sandbox_manager()
    assert first is second
    resolver_mod.reset_sandbox_manager()
    assert resolver_mod.get_sandbox_manager() is not first


def test_get_sandbox_manager_none_when_disabled(monkeypatch) -> None:
    monkeypatch.setattr(resolver_mod.settings, "sandbox_enabled", False)
    resolver_mod.reset_sandbox_manager()
    assert resolver_mod.get_sandbox_manager() is None
    resolver_mod.reset_sandbox_manager()
