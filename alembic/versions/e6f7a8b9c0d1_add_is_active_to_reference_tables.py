"""add is_active to divisions, currencies and document_types

Revision ID: e6f7a8b9c0d1
Revises: d5e6f7a8b9c0
Create Date: 2026-10-19 15:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'e6f7a8b9c0d1'
down_revision: Union[str, Sequence[str], None] = 'd5e6f7a8b9c0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Выравнивание поведения справочников: у агентов и компаний-покупателей
# деактивация уже была, теперь она есть у подразделений, валют и типов
# документов. Существующие записи считаются активными (server_default).
_TABLES = ["divisions", "currencies", "document_types"]


def upgrade() -> None:
    for table in _TABLES:
        op.add_column(
            table,
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        )


def downgrade() -> None:
    for table in reversed(_TABLES):
        op.drop_column(table, "is_active")
