"""add payment_executions table and EXECUTION_RECORDED audit action

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-10-16 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'e5f6a7b8c9d0'
down_revision: Union[str, Sequence[str], None] = 'd4e5f6a7b8c9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """BR-080/081 (ADR-006, вариант A): фактическое исполнение — отдельная сущность."""
    op.create_table(
        'payment_executions',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('payment_request_id', sa.Integer(), nullable=False),
        sa.Column('executed_at', sa.Date(), nullable=False),
        sa.Column('amount', sa.Numeric(precision=18, scale=2), nullable=False),
        sa.Column('currency_id', sa.Integer(), nullable=False),
        sa.Column('rate', sa.Numeric(precision=18, scale=6), nullable=True),
        sa.Column('amount_rub', sa.Numeric(precision=18, scale=2), nullable=True),
        sa.Column('method', postgresql.ENUM('BANK', 'AGENT', name='payment_method', create_type=False), nullable=True),
        sa.Column('operation_id', sa.String(length=128), nullable=True),
        sa.Column('comment', sa.Text(), nullable=True),
        sa.Column('created_by_id', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['currency_id'], ['currencies.id'], name=op.f('fk_payment_executions_currency_id_currencies')),
        sa.ForeignKeyConstraint(['created_by_id'], ['users.id'], name=op.f('fk_payment_executions_created_by_id_users')),
        sa.ForeignKeyConstraint(['payment_request_id'], ['payment_requests.request_id'], name=op.f('fk_payment_executions_payment_request_id_payment_requests'), ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_payment_executions'))
    )
    op.create_index(op.f('ix_payment_executions_payment_request_id'), 'payment_executions', ['payment_request_id'], unique=False)
    op.execute("ALTER TYPE audit_action_type ADD VALUE IF NOT EXISTS 'EXECUTION_RECORDED'")


def downgrade() -> None:
    """Откат."""
    op.drop_index(op.f('ix_payment_executions_payment_request_id'), table_name='payment_executions')
    op.drop_table('payment_executions')
    # значение enum не удаляем (append-only, как в d4e5f6a7b8c9)