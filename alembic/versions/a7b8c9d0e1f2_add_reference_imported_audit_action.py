"""add REFERENCE_IMPORTED audit action

Revision ID: a7b8c9d0e1f2
Revises: f6a7b8c9d0e1
Create Date: 2026-10-16 01:00:00.000000

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'a7b8c9d0e1f2'
down_revision: Union[str, Sequence[str], None] = 'f6a7b8c9d0e1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Значение для аудита импорта справочников (append-only)."""
    op.execute("ALTER TYPE audit_action_type ADD VALUE IF NOT EXISTS 'REFERENCE_IMPORTED'")


def downgrade() -> None:
    """Не удаляем значения enum (append-only журнал)."""
    pass