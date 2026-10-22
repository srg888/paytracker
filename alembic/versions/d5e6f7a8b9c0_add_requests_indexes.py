"""add indexes on requests for list filtering

Revision ID: d5e6f7a8b9c0
Revises: c4d5e6f7a8b9
Create Date: 2026-10-19 14:00:00.000000

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'd5e6f7a8b9c0'
down_revision: Union[str, Sequence[str], None] = 'c4d5e6f7a8b9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Индексы под реальные запросы списка и отчётов (см. vault:
# «Масштабирование списка заявок»). Без них даже выборка заявок одного
# Заказчика шла через Seq Scan по всей таблице.
_INDEXES = [
    ("ix_requests_requester_id", "requester_id"),
    ("ix_requests_created_by_id", "created_by_id"),
    ("ix_requests_executor_id", "executor_id"),
    ("ix_requests_division_id", "division_id"),
    ("ix_requests_status", "status"),
    ("ix_requests_expected_date", "expected_date"),
]


def upgrade() -> None:
    for name, column in _INDEXES:
        op.create_index(name, "requests", [column], unique=False)


def downgrade() -> None:
    for name, _ in reversed(_INDEXES):
        op.drop_index(name, table_name="requests")
