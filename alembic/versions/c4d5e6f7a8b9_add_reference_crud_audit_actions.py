"""add reference_created/updated/deleted audit actions

Revision ID: c4d5e6f7a8b9
Revises: f2a3b4c5d6e7
Create Date: 2026-10-19 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'c4d5e6f7a8b9'
down_revision: Union[str, Sequence[str], None] = 'f2a3b4c5d6e7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Администрирование справочников: создание/изменение/удаление записи
    справочника пишется в аудит (vault: «Администрирование справочников»)."""
    op.execute("ALTER TYPE audit_action_type ADD VALUE IF NOT EXISTS 'REFERENCE_CREATED'")
    op.execute("ALTER TYPE audit_action_type ADD VALUE IF NOT EXISTS 'REFERENCE_UPDATED'")
    op.execute("ALTER TYPE audit_action_type ADD VALUE IF NOT EXISTS 'REFERENCE_DELETED'")


def downgrade() -> None:
    """Не удаляем значения enum (append-only журнал)."""
    pass
