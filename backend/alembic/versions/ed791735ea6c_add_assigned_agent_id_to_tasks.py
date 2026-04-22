"""add_assigned_agent_id_to_tasks

Revision ID: ed791735ea6c
Revises: c3d4e5f6a7b8
Create Date: 2026-04-14 05:54:43.434702

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'ed791735ea6c'
down_revision: Union[str, Sequence[str], None] = 'c3d4e5f6a7b8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add assigned_agent_id column for passive task dispatch."""
    op.add_column('tasks', sa.Column('assigned_agent_id', sa.Uuid(), nullable=True))
    op.create_foreign_key(
        'fk_tasks_assigned_agent_id',
        'tasks', 'agents',
        ['assigned_agent_id'], ['id'],
        ondelete='SET NULL',
    )


def downgrade() -> None:
    """Remove assigned_agent_id column."""
    op.drop_constraint('fk_tasks_assigned_agent_id', 'tasks', type_='foreignkey')
    op.drop_column('tasks', 'assigned_agent_id')
