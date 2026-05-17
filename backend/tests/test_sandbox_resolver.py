"""Part B / PR 5 — sandbox manager resolver.

``sandbox_enabled`` decides Docker vs the host-side Noop manager.
"""

from __future__ import annotations

from backend.src.core.sandbox import DockerSandboxManager, NoopSandboxManager
from backend.src.core.sandbox import resolver as resolver_mod


def test_build_returns_noop_when_sandbox_disabled(monkeypatch) -> None:
    monkeypatch.setattr(resolver_mod.settings, "sandbox_enabled", False)
    assert isinstance(resolver_mod.build_sandbox_manager(), NoopSandboxManager)


def test_build_returns_docker_when_sandbox_enabled(monkeypatch) -> None:
    monkeypatch.setattr(resolver_mod.settings, "sandbox_enabled", True)
    monkeypatch.setattr(resolver_mod.settings, "docker_host", "tcp://dind:2375")
    mgr = resolver_mod.build_sandbox_manager()
    assert isinstance(mgr, DockerSandboxManager)


def test_get_sandbox_manager_is_cached(monkeypatch) -> None:
    monkeypatch.setattr(resolver_mod.settings, "sandbox_enabled", False)
    resolver_mod.reset_sandbox_manager()
    first = resolver_mod.get_sandbox_manager()
    second = resolver_mod.get_sandbox_manager()
    assert first is second
    resolver_mod.reset_sandbox_manager()
    assert resolver_mod.get_sandbox_manager() is not first
