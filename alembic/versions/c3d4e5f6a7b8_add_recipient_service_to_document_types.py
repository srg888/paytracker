"""add recipient_service to document_types (BR-123)

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-10-15 22:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'c3d4e5f6a7b8'
down_revision: Union[str, Sequence[str], None] = 'b2c3d4e5f6a7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """BR-123: служба-получатель для типа документа."""
    op.add_column('document_types', sa.Column('recipient_service', sa.String(length=128), nullable=True))


def downgrade() -> None:
    """Откат."""
    op.drop_column('document_types', 'recipient_service')