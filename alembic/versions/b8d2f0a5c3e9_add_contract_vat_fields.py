"""add contract_id/commission_includes_vat to payment_requests and
payment_terms_proposals; add CURRENCY_CONTROL_THRESHOLD_TRIGGERED audit value

Revision ID: b8d2f0a5c3e9
Revises: a7c1e9f4b2d8
Create Date: 2026-10-30 00:05:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'b8d2f0a5c3e9'
down_revision: Union[str, Sequence[str], None] = 'a7c1e9f4b2d8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """БР-131: привязка заявки/предложения условий к договору агента, и флаг
    'комиссия с НДС или без' — нужен для отчёта А2 (аналитика по агентам)."""
    op.add_column('payment_requests', sa.Column('contract_id', sa.Integer(), nullable=True))
    op.add_column('payment_requests', sa.Column('commission_includes_vat', sa.Boolean(), nullable=True))
    op.create_foreign_key(
        op.f('fk_payment_requests_contract_id_agent_contracts'),
        'payment_requests', 'agent_contracts', ['contract_id'], ['id'],
    )

    op.add_column('payment_terms_proposals', sa.Column('proposed_contract_id', sa.Integer(), nullable=True))
    op.add_column('payment_terms_proposals', sa.Column('commission_includes_vat', sa.Boolean(), nullable=True))
    op.create_foreign_key(
        op.f('fk_payment_terms_proposals_proposed_contract_id_agent_contracts'),
        'payment_terms_proposals', 'agent_contracts', ['proposed_contract_id'], ['id'],
    )

    op.execute("ALTER TYPE audit_action_type ADD VALUE IF NOT EXISTS 'CURRENCY_CONTROL_THRESHOLD_TRIGGERED'")


def downgrade() -> None:
    """Откат (значение enum не удаляем — append-only журнал)."""
    op.drop_constraint(
        op.f('fk_payment_terms_proposals_proposed_contract_id_agent_contracts'),
        'payment_terms_proposals', type_='foreignkey',
    )
    op.drop_column('payment_terms_proposals', 'commission_includes_vat')
    op.drop_column('payment_terms_proposals', 'proposed_contract_id')

    op.drop_constraint(
        op.f('fk_payment_requests_contract_id_agent_contracts'),
        'payment_requests', type_='foreignkey',
    )
    op.drop_column('payment_requests', 'commission_includes_vat')
    op.drop_column('payment_requests', 'contract_id')
