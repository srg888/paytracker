"""add reports table

Revision ID: deb9cc9ff11e
Revises: 046f4c44b3c9
Create Date: 2026-10-14 21:46:00.144373

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'deb9cc9ff11e'
down_revision: Union[str, Sequence[str], None] = '046f4c44b3c9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'reports',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('request_id', sa.Integer(), nullable=False),
        sa.Column('export_format', sa.Enum('csv', 'xlsx', name='export_format'), nullable=False),
        sa.Column('file_path', sa.String(length=1024), nullable=False),
        sa.Column('file_size_bytes', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('created_by_id', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(['request_id'], ['requests.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['created_by_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_reports_request_id'), 'reports', ['request_id'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_reports_request_id'), table_name='reports')
    op.drop_table('reports')
    # Снять и ENUM-тип, иначе повторный upgrade упадёт с
    # "type export_format already exists" (это ловит CI).
    op.execute('DROP TYPE IF EXISTS export_format')
