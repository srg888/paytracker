"""add missing audit_action_type enum values

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-10-15 22:45:00.000000

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'd4e5f6a7b8c9'
down_revision: Union[str, Sequence[str], None] = 'c3d4e5f6a7b8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Значения, объявленные в app/models/enums.py, но отсутствующие в БД
_NEW_VALUES = [
    "EXECUTOR_SELF_ASSIGNED",
    "TERMS_PROPOSED",
    "TERMS_ACCEPTED",
    "TERMS_REJECTED",
    "SENT_FOR_MANAGER_REVIEW",
    "REWORK_REQUESTED",
    "CLOSED_BY_MANAGER",
    "CREATED_FOR_REQUESTER",
    "COMMENT_ATTACHMENT_UPLOADED",
    "REPORT_GENERATED",
]

# Недостающие значения enum request_status (добавлены в коде позже, чем создан тип)
_NEW_STATUS_VALUES = [
    "TERMS_PROPOSED",
    "MANAGER_REVIEW",
]


def upgrade() -> None:
    """Добавить недостающие значения в enum'ы (append-only)."""
    for val in _NEW_VALUES:
        op.execute(
            f"ALTER TYPE audit_action_type ADD VALUE IF NOT EXISTS '{val}'"
        )
    for val in _NEW_STATUS_VALUES:
        op.execute(
            f"ALTER TYPE request_status ADD VALUE IF NOT EXISTS '{val}'"
        )


def downgrade() -> None:
    """Postgres не позволяет удалять значения enum — downgrade необратим без
    пересоздания типа. Оставляем значения (append-only журнал аудита допускает)."""
    pass
