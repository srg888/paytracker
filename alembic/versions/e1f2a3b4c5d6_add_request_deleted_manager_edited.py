"""add REQUEST_DELETED and MANAGER_EDITED_REQUEST audit action values

Revision ID: e1f2a3b4c5d6
Revises: d8e9f0a1b2c3
Create Date: 2026-10-18 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'e1f2a3b4c5d6'
down_revision: Union[str, Sequence[str], None] = 'd8e9f0a1b2c3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """BR-018: Руководитель может удалить заявку (одну/несколько) и редактировать
    любую заявку — оба действия пишутся в аудит новыми типами событий."""
    op.execute("ALTER TYPE audit_action_type ADD VALUE IF NOT EXISTS 'REQUEST_DELETED'")
    op.execute("ALTER TYPE audit_action_type ADD VALUE IF NOT EXISTS 'MANAGER_EDITED_REQUEST'")


def downgrade() -> None:
    """Не удаляем значения enum (append-only журнал)."""
    pass
