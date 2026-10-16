"""add delegation.reason, agent.is_own_company, exchange_rate.source

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-10-16 00:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'f6a7b8c9d0e1'
down_revision: Union[str, Sequence[str], None] = 'e5f6a7b8c9d0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Пункт 6 (мелочи): причина делегирования (BR-032), признак 'наша компания'
    у агентов, источник курса (BR-061)."""
    op.add_column('delegations', sa.Column('reason', sa.Text(), nullable=True))
    op.add_column('agents', sa.Column('is_own_company', sa.Boolean(), server_default=sa.text('false'), nullable=False))
    op.add_column('exchange_rates', sa.Column('source', sa.String(length=16), nullable=True))


def downgrade() -> None:
    """Откат."""
    op.drop_column('delegations', 'reason')
    op.drop_column('agents', 'is_own_company')
    op.drop_column('exchange_rates', 'source')