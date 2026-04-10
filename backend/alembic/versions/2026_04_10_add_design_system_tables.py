"""add design_systems and design_screens tables

Revision ID: c8d9e0f1a2b3
Revises: b7c8d9e0f1a2
Create Date: 2026-04-10 12:45:00.000000

Foundation for the builtin design tool that replaces the Stitch MCP
integration. The Designer agent owns these tables; routes will be added
in subsequent commits.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c8d9e0f1a2b3"
down_revision: str | Sequence[str] | None = "b7c8d9e0f1a2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "design_systems",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("project_id", sa.Uuid(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(255), nullable=False, server_default="Default"),
        sa.Column("tokens", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("components", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("patterns", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("brand_voice", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_design_systems_project", "design_systems", ["project_id"])

    op.create_table(
        "design_screens",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("project_id", sa.Uuid(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column(
            "design_system_id",
            sa.Uuid(),
            sa.ForeignKey("design_systems.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("route", sa.String(500), nullable=True),
        sa.Column("intent", sa.Text(), nullable=True),
        sa.Column("spec", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("generated_code_path", sa.String(500), nullable=True),
        sa.Column("preview_image_path", sa.String(500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_design_screens_project", "design_screens", ["project_id"])
    op.create_index("ix_design_screens_design_system", "design_screens", ["design_system_id"])


def downgrade() -> None:
    op.drop_index("ix_design_screens_design_system", table_name="design_screens")
    op.drop_index("ix_design_screens_project", table_name="design_screens")
    op.drop_table("design_screens")
    op.drop_index("ix_design_systems_project", table_name="design_systems")
    op.drop_table("design_systems")
