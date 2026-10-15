"""add subagent and execution deadline to terms proposals and payment requests

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-10-15 22:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'b2c3d4e5f6a7'
down_revision: Union[str, Sequence[str], None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """BR-120/121: субагент (второй уровень маршрута) и срок исполнения
    в предложении условий; утверждённый субагент — в платёжной заявке."""
    op.add_column('payment_terms_proposals', sa.Column('proposed_subagent_id', sa.Integer(), nullable=True))
    op.add_column('payment_terms_proposals', sa.Column('proposed_execution_deadline', sa.Date(), nullable=True))
    op.add_column('payment_requests', sa.Column('subagent_id', sa.Integer(), nullable=True))
    op.add_column('payment_requests', sa.Column('agreed_execution_deadline', sa.Date(), nullable=True))

    op.create_foreign_key(
        op.f('fk_payment_terms_proposals_proposed_subagent_id_agents'),
        'payment_terms_proposals', 'agents', ['proposed_subagent_id'], ['id'],
    )
    op.create_foreign_key(
        op.f('fk_payment_requests_subagent_id_agents'),
        'payment_requests', 'agents', ['subagent_id'], ['id'],
    )


def downgrade() -> None:
    """Откат."""
    op.drop_constraint(op.f('fk_payment_requests_subagent_id_agents'), 'payment_requests', type_='foreignkey')
    op.drop_constraint(
        op.f('fk_payment_terms_proposals_proposed_subagent_id_agents'),
        'payment_terms_proposals', type_='foreignkey',
    )
    op.drop_column('payment_terms_proposals', 'proposed_execution_deadline')
    op.drop_column('payment_terms_proposals', 'proposed_subagent_id')
    op.drop_column('payment_requests', 'subagent_id')
    op.drop_column('payment_requests', 'agreed_execution_deadline')
