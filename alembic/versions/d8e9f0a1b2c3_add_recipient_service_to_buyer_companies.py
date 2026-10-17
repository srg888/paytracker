"""add recipient_service to buyer_companies and agents

Revision ID: d8e9f0a1b2c3
Revises: a7b8c9d0e1f2
Create Date: 2026-10-17 06:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'd8e9f0a1b2c3'
down_revision: Union[str, Sequence[str], None] = 'a7b8c9d0e1f2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('buyer_companies', sa.Column('recipient_service', sa.String(length=255), nullable=True))


def downgrade() -> None:
    op.drop_column('buyer_companies', 'recipient_service')
