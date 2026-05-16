"""Tier 5 — per-resource REST authorization gates.

BSNexus had ZERO authorization gates before Tier 5 — every REST route
ran only ``get_current_user`` (authentication, no authorization). This
suite pins that every ``/api/v1`` route now carries a
``require_permission`` dependency mapped to the shared
``bsvibe-authz`` permission matrix
(``packages/bsvibe-authz/schema/permission_matrix.yaml``), and that the
gate actually enforces a 403 when OpenFGA denies.

Self/liveness routes (``/health``, ``/health/deps``, ``/api/v1/auth/*``)
are intentionally auth-only and excluded.
"""

from __future__ import annotations

import inspect

import pytest

from backend.src.core.auth import require_permission
from backend.src.main import create_app

# Routes that are intentionally NOT permission-gated: liveness probes and
# the self/session endpoints (a user must be able to read their own
# profile / log out without a tenant permission).
_UNGATED_PATHS = {
    "/health",
    "/health/deps",
    "/mcp/health",
    "/auth/callback",
    "/api/v1/auth/refresh",
    "/api/v1/auth/me",
    "/api/v1/auth/logout",
}

# Expected permission string per (method, path). The single source of
# truth is the bsnexus block of the bsvibe-authz permission matrix.
_EXPECTED_GATES: dict[tuple[str, str], str] = {
    ("GET", "/api/v1/projects"): "bsnexus.projects.read",
    ("POST", "/api/v1/projects"): "bsnexus.projects.write",
    ("GET", "/api/v1/projects/{project_id}"): "bsnexus.projects.read",
    ("PATCH", "/api/v1/projects/{project_id}"): "bsnexus.projects.write",
    ("DELETE", "/api/v1/projects/{project_id}"): "bsnexus.projects.delete",
    ("GET", "/api/v1/requests"): "bsnexus.requests.read",
    ("POST", "/api/v1/directions"): "bsnexus.directions.write",
    ("GET", "/api/v1/decisions"): "bsnexus.decisions.read",
    ("POST", "/api/v1/decisions/{decision_id}/resolve"): "bsnexus.decisions.write",
    ("POST", "/api/v1/deliverables"): "bsnexus.deliverables.write",
    ("GET", "/api/v1/deliverables"): "bsnexus.deliverables.read",
    ("POST", "/api/v1/deliverables/{deliverable_id}/verify"): "bsnexus.deliverables.write",
    ("GET", "/api/v1/brief"): "bsnexus.brief.read",
    ("GET", "/api/v1/events"): "bsnexus.events.read",
    ("GET", "/api/v1/workspace-files"): "bsnexus.workspace_files.read",
    ("GET", "/api/v1/workspace-files/content"): "bsnexus.workspace_files.read",
    ("GET", "/api/v1/integrations"): "bsnexus.integrations.read",
    ("PATCH", "/api/v1/integrations/{provider}"): "bsnexus.integrations.write",
    ("POST", "/api/v1/integrations/{provider}/test"): "bsnexus.integrations.write",
    ("GET", "/api/v1/executor-config"): "bsnexus.executor_config.read",
    ("PUT", "/api/v1/executor-config"): "bsnexus.executor_config.write",
    ("GET", "/api/v1/repo-config"): "bsnexus.repo_config.read",
    ("PUT", "/api/v1/repo-config"): "bsnexus.repo_config.write",
    ("DELETE", "/api/v1/repo-config"): "bsnexus.repo_config.delete",
    # repo_branch has no `.write` row in the matrix; the mutating POST is
    # gated on `.read` (admin-surface — read already requires admin).
    ("POST", "/api/v1/requests/{request_id}/branch"): "bsnexus.repo_branch.read",
    ("POST", "/api/v1/requests/{request_id}/pr"): "bsnexus.repo_pull_request.write",
}


def _gate_permission_for_route(route) -> str | None:
    """Return the permission string of the ``require_permission`` gate on
    ``route`` by reading the closure of the gate's ``_dep`` callable.

    The Tier-5 gate is a closure ``_dep`` built by
    ``bsvibe_authz.deps.require_permission``; the ``permission`` triple
    is captured in its enclosing scope. Walk the route's dependency tree
    and read the free variable.
    """

    def _walk(dependant) -> str | None:
        for sub in dependant.dependencies:
            call = sub.call
            closure = inspect.getclosurevars(call) if callable(call) else None
            if closure is not None:
                perm = closure.nonlocals.get("permission")
                if isinstance(perm, str) and perm.count(".") == 2:
                    return perm
            found = _walk(sub)
            if found is not None:
                return found
        return None

    return _walk(route.dependant)


def _api_routes(app):
    for route in app.routes:
        path = getattr(route, "path", "")
        if not hasattr(route, "dependant"):
            continue
        if path in _UNGATED_PATHS:
            continue
        if not (path.startswith("/api/v1") or path.startswith("/api/")):
            continue
        for method in sorted(getattr(route, "methods", set()) - {"HEAD", "OPTIONS"}):
            yield method, path, route


def test_every_api_route_has_a_require_permission_gate():
    """No /api/v1 route may be authentication-only — Tier 5 closes the
    29-route authz gap. A new ungated route fails this test."""
    app = create_app(cors_origins=[], rate_limit=False)
    ungated: list[str] = []
    for method, path, route in _api_routes(app):
        if _gate_permission_for_route(route) is None:
            ungated.append(f"{method} {path}")
    assert not ungated, f"routes missing require_permission gate: {ungated}"


def test_route_gates_match_the_permission_matrix():
    """Each gated route carries the exact ``bsnexus.<resource>.<action>``
    string from the bsvibe-authz permission matrix."""
    app = create_app(cors_origins=[], rate_limit=False)
    actual: dict[tuple[str, str], str] = {}
    for method, path, route in _api_routes(app):
        perm = _gate_permission_for_route(route)
        if perm is not None:
            actual[(method, path)] = perm
    assert actual == _EXPECTED_GATES


def test_self_and_liveness_routes_stay_ungated():
    """``/health`` and ``/api/v1/auth/*`` must NOT carry a permission
    gate — they are liveness / self-session routes."""
    app = create_app(cors_origins=[], rate_limit=False)
    for route in app.routes:
        path = getattr(route, "path", "")
        if path in {"/health", "/health/deps", "/api/v1/auth/me", "/api/v1/auth/logout"}:
            assert _gate_permission_for_route(route) is None, f"{path} unexpectedly gated"


@pytest.mark.asyncio
async def test_require_permission_enforces_403_when_openfga_denies(monkeypatch):
    """With OpenFGA configured and ``check`` returning ``False`` the gate
    raises 403 — proves the gate is a real enforcement point, not a
    no-op decoration."""
    import backend.src.core.auth as auth_mod
    from bsvibe_authz import deps as authz_deps
    from bsvibe_authz.settings import Settings as AuthzSettings

    # Build a settings object with OpenFGA "configured" so the gate
    # leaves permissive mode and runs the check.
    enforcing = AuthzSettings(
        openfga_api_url="http://openfga.local",
        openfga_store_id="store",
        openfga_auth_model_id="model",
        service_token_signing_secret="x" * 32,
    )
    monkeypatch.setattr(auth_mod, "_authz_settings", lambda: enforcing)
    monkeypatch.setattr(authz_deps, "get_settings", lambda: enforcing)

    class _DenyFGA:
        async def check(self, user, relation, object_):  # noqa: ANN001
            return False

        async def list_objects(self, user, relation, type_):  # noqa: ANN001
            return []

        async def write_tuple(self, user, relation, object_):  # noqa: ANN001
            return None

    authz_deps.reset_singletons()
    monkeypatch.setattr(authz_deps, "get_openfga_client", lambda *a, **k: _DenyFGA())

    gate = require_permission("bsnexus.projects.read")

    # Resolve the gate's inner _dep manually. It depends on a Request,
    # the principal, settings, cache and fga client.
    from fastapi import HTTPException

    from backend.src.core.tenant_context import BSVibeUser

    user = BSVibeUser(
        id="u-deny",
        email="deny@example.com",
        app_metadata={"role": "viewer", "tenant_id": "11111111-1111-1111-1111-111111111111"},
        user_metadata={},
    )
    principal = await auth_mod._authz_principal(user)
    cache = authz_deps.get_permission_cache(settings=enforcing)

    class _Req:
        path_params: dict = {}

    with pytest.raises(HTTPException) as exc:
        # gate's _dep signature: (request, user, settings, cache, fga)
        await gate(
            request=_Req(),
            user=principal,
            settings=enforcing,
            cache=cache,
            fga=_DenyFGA(),
        )
    assert exc.value.status_code == 403
    authz_deps.reset_singletons()
