"""S4 — Tenant isolation regression scenarios across founder-metaphor APIs.

Sprint 0 closed the C3 vulnerability (JWT spoofing). Sprint 4 adds a
regression suite that exercises **every founder-metaphor read path**
against a foreign-tenant row to pin the 404-not-200 contract.

Audit §6 explicitly cites tenant-isolation as a coverage gap because
the original test_jwt_security.py focuses on Project list/create. The
other paths (Request, Deliverable, Decision, ExecutionRun list,
CompositionSnapshot) need the same regression guarantee.
"""

from __future__ import annotations

import uuid

import pytest

from backend.src.models import (
    CompositionSnapshot,
    CompositionSource,
    Decision,
    ExecutionRun,
    Project,
    Request,
    RequestStatus,
    RunStatus,
    Tenant,
)


async def _foreign_project(db_session) -> tuple[uuid.UUID, Project]:
    other_tid = uuid.uuid4()
    db_session.add(
        Tenant(
            id=other_tid,
            name="Foreign",
            slug=f"f-{other_tid.hex[:8]}",
            owner_user_id="x",
        )
    )
    await db_session.commit()
    project = Project(tenant_id=other_tid, name="Hidden", description="secret")
    db_session.add(project)
    await db_session.commit()
    await db_session.refresh(project)
    return other_tid, project


# ── Foreign-tenant list endpoints return empty / 404 ──────────────


@pytest.mark.asyncio
async def test_list_requests_for_foreign_project_returns_404(client, db_session) -> None:
    """``GET /projects/{id}/requests`` for a foreign-tenant project must
    404 — the project is invisible to this tenant, even if its
    sub-resources exist."""
    _foreign_tid, project = await _foreign_project(db_session)
    resp = await client.get(
        f"/api/v1/projects/{project.id}/requests",
        headers={"Authorization": "Bearer fake"},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_list_deliverables_for_foreign_project_returns_404(client, db_session) -> None:
    _foreign_tid, project = await _foreign_project(db_session)
    resp = await client.get(
        f"/api/v1/projects/{project.id}/deliverables",
        headers={"Authorization": "Bearer fake"},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_list_decisions_for_foreign_project_returns_404(client, db_session) -> None:
    _foreign_tid, project = await _foreign_project(db_session)
    resp = await client.get(
        f"/api/v1/projects/{project.id}/decisions",
        headers={"Authorization": "Bearer fake"},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_get_foreign_composition_snapshot_returns_404(client, db_session) -> None:
    """The Inside panel reads CompositionSnapshot directly. Without
    tenant scoping, an attacker could enumerate snapshots across
    tenants by guessing UUIDs (C3 surface area)."""
    foreign_tid, project = await _foreign_project(db_session)

    request = Request(
        tenant_id=foreign_tid,
        project_id=project.id,
        intent_summary="hidden",
        status=RequestStatus.open,
    )
    db_session.add(request)
    await db_session.flush()

    snapshot = CompositionSnapshot(
        tenant_id=foreign_tid,
        request_id=request.id,
        source=CompositionSource.local,
        system_prompt_ref={"inline": "secret prompt"},
        tools_allowed=[],
        context_doc_refs=[],
        persona_label="hidden",
    )
    db_session.add(snapshot)
    await db_session.commit()
    await db_session.refresh(snapshot)

    resp = await client.get(
        f"/api/v1/composition-snapshots/{snapshot.id}",
        headers={"Authorization": "Bearer fake"},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_list_runs_for_foreign_request_returns_404(client, db_session) -> None:
    foreign_tid, project = await _foreign_project(db_session)
    request = Request(
        tenant_id=foreign_tid,
        project_id=project.id,
        intent_summary="hidden",
        status=RequestStatus.open,
    )
    db_session.add(request)
    await db_session.flush()

    db_session.add(
        ExecutionRun(
            tenant_id=foreign_tid,
            project_id=project.id,
            request_id=request.id,
            status=RunStatus.running,
        )
    )
    await db_session.commit()
    await db_session.refresh(request)

    resp = await client.get(
        f"/api/v1/requests/{request.id}/runs",
        headers={"Authorization": "Bearer fake"},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_resolve_foreign_decision_returns_404(client, db_session) -> None:
    foreign_tid, project = await _foreign_project(db_session)
    decision = Decision(
        tenant_id=foreign_tid,
        project_id=project.id,
        question="secret",
        options=[],
        blocking=True,
    )
    db_session.add(decision)
    await db_session.commit()
    await db_session.refresh(decision)

    resp = await client.post(
        f"/api/v1/decisions/{decision.id}/resolve",
        json={"resolution": "x"},
        headers={"Authorization": "Bearer fake"},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_list_requests_does_not_leak_foreign_rows(client, db_session, mock_tenant_id) -> None:
    """List endpoint at the project level must not surface foreign
    tenant rows even when only one rogue row exists in the DB."""
    foreign_tid, foreign_project = await _foreign_project(db_session)

    # Create a Request under the foreign tenant.
    db_session.add(
        Request(
            tenant_id=foreign_tid,
            project_id=foreign_project.id,
            intent_summary="should not appear",
            status=RequestStatus.open,
        )
    )
    await db_session.commit()

    # Make the user's own project + request.
    own_resp = await client.post(
        "/api/v1/projects",
        json={"name": "Mine"},
        headers={"Authorization": "Bearer fake"},
    )
    own_pid = own_resp.json()["id"]
    db_session.add(
        Request(
            tenant_id=mock_tenant_id,
            project_id=uuid.UUID(own_pid),
            intent_summary="my own",
            status=RequestStatus.open,
        )
    )
    await db_session.commit()

    # Listing under the user's own project returns only their own row.
    rows = (
        await client.get(
            f"/api/v1/projects/{own_pid}/requests",
            headers={"Authorization": "Bearer fake"},
        )
    ).json()
    summaries = [r["intent_summary"] for r in rows]
    assert "my own" in summaries
    assert "should not appear" not in summaries


# ── Integration config — never exposes another tenant's encrypted key


@pytest.mark.asyncio
async def test_integrations_list_does_not_expose_foreign_config(client, db_session) -> None:
    """The integrations list endpoint must scope by tenant. A foreign
    tenant's BSage config (with encrypted api_key) must NEVER appear in
    the redacted-list response, even if the foreign row exists in DB."""
    from backend.src.models import TenantIntegrationConfig
    from backend.src.models.tenant_integration_config import IntegrationProvider

    foreign_tid, _project = await _foreign_project(db_session)
    db_session.add(
        TenantIntegrationConfig(
            tenant_id=foreign_tid,
            provider=IntegrationProvider.bsage,
            enabled=True,
            base_url="http://victim",
            api_key_encrypted="encrypted-secret",
        )
    )
    await db_session.commit()

    resp = await client.get(
        "/api/v1/integrations",
        headers={"Authorization": "Bearer fake"},
    )
    assert resp.status_code == 200
    data = resp.json()
    # The user's own tenant has no BSage row → defaults to disabled.
    assert data["bsage"]["enabled"] is False
    assert data["bsage"]["base_url"] is None
    # And the API never exposes the encrypted key directly.
    assert "api_key_encrypted" not in str(data)
    assert "api_key" not in data["bsage"]
