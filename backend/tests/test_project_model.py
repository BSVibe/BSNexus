"""Project model invariants.

- Projects are tenant-scoped via ``tenant_id`` (NOT NULL).
- Deleting the tenant cascades to projects.
- New optional columns for the founder metaphor are present and nullable.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from backend.src.models import Project, Tenant


@pytest.mark.asyncio
async def test_project_requires_tenant_id(db_session, seeded_tenant):
    """Creating a project without tenant_id should fail."""
    project = Project(
        name="Unscoped",
        description="",
    )
    db_session.add(project)
    with pytest.raises(IntegrityError):
        await db_session.commit()


@pytest.mark.asyncio
async def test_project_scoped_to_tenant(db_session, seeded_tenant):
    project = Project(
        tenant_id=seeded_tenant.id,
        name="Scoped",
        description="",
    )
    db_session.add(project)
    await db_session.commit()
    await db_session.refresh(project)

    assert project.tenant_id == seeded_tenant.id


@pytest.mark.asyncio
async def test_project_exposes_bsage_and_bsupervisor_hints(db_session, seeded_tenant):
    project = Project(
        tenant_id=seeded_tenant.id,
        name="With hints",
        description="",
        bsage_workspace_id="workspace-123",
        bsupervisor_policy_id="policy-abc",
    )
    db_session.add(project)
    await db_session.commit()
    await db_session.refresh(project)

    assert project.bsage_workspace_id == "workspace-123"
    assert project.bsupervisor_policy_id == "policy-abc"


@pytest.mark.asyncio
async def test_deleting_tenant_cascades_projects(db_session):
    tid = uuid.uuid4()
    tenant = Tenant(
        id=tid,
        name="Ephemeral",
        slug=f"e-{tid.hex[:8]}",
        owner_user_id="owner",
    )
    db_session.add(tenant)
    await db_session.commit()

    db_session.add(Project(tenant_id=tenant.id, name="Gone soon", description=""))
    await db_session.commit()

    await db_session.delete(tenant)
    await db_session.commit()

    remaining = (await db_session.execute(select(Project))).scalars().all()
    assert remaining == []
