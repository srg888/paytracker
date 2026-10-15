"""make reports.request_id nullable (list reports have no single request)

Revision ID: a1b2c3d4e5f6
Revises: deb9cc9ff11e
Create Date: 2026-10-15 19:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, Sequence[str], None] = 'deb9cc9ff11e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Разрешить NULL в request_id — отчёты по списку не привязаны к заявке."""
    op.alter_column('reports', 'request_id', existing_type=sa.Integer(), nullable=True)


def downgrade() -> None:
    """Вернуть NOT NULL (удалить строки без request_id перед этим)."""
    op.execute("DELETE FROM reports WHERE request_id IS NULL")
    op.alter_column('reports', 'request_id', existing_type=sa.Integer(), nullable=False)