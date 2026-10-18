"""add REQUEST_IMPORTED audit action value

Revision ID: f2a3b4c5d6e7
Revises: e1f2a3b4c5d6
Create Date: 2026-10-18 01:00:00.000000

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'f2a3b4c5d6e7'
down_revision: Union[str, Sequence[str], None] = 'e1f2a3b4c5d6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Для разового скрипта переноса старого Excel-реестра платежей
    (scripts/import_legacy_payment_register.py) — чтобы импортированные
    заявки были отличимы в аудите от созданных через интерфейс."""
    op.execute("ALTER TYPE audit_action_type ADD VALUE IF NOT EXISTS 'REQUEST_IMPORTED'")


def downgrade() -> None:
    """Не удаляем значения enum (append-only журнал)."""
    pass
