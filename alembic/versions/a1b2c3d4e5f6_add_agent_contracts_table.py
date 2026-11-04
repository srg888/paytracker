"""add agent_contracts table

Revision ID: a1b2c3d4e5f6
Revises: e6f7a8b9c0d1
Create Date: 2026-10-30 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, Sequence[str], None] = 'e6f7a8b9c0d1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """БР-131: договоры с агентом — учёт оборотов/вознаграждений и порог
    валютного контроля ведутся в разрезе конкретного договора, не агента
    в целом (у одного агента может быть несколько договоров)."""
    op.create_table(
        'agent_contracts',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('agent_id', sa.Integer(), nullable=False),
        sa.Column('number', sa.String(length=100), nullable=False),
        sa.Column('start_date', sa.Date(), nullable=False),
        sa.Column('end_date', sa.Date(), nullable=True),
        sa.Column('currency_control_registered', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['agent_id'], ['agents.id'], name=op.f('fk_agent_contracts_agent_id_agents')),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_agent_contracts')),
    )
    op.create_index(op.f('ix_agent_contracts_agent_id'), 'agent_contracts', ['agent_id'], unique=False)


def downgrade() -> None:
    """Откат."""
    op.drop_index(op.f('ix_agent_contracts_agent_id'), table_name='agent_contracts')
    op.drop_table('agent_contracts')
