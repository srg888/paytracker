"""add_requester_id_terms_proposals_and_agreed_fields

Revision ID: 046f4c44b3c9
Revises: 6aaeeb74725b
Create Date: 2026-07-20 20:02:02.916115

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '046f4c44b3c9'
down_revision: Union[str, Sequence[str], None] = '6aaeeb74725b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('payment_terms_proposals',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('payment_request_id', sa.Integer(), nullable=False),
    sa.Column('proposed_payment_method', postgresql.ENUM('BANK', 'AGENT', name='payment_method', create_type=False), nullable=False),
    sa.Column('proposed_agent_id', sa.Integer(), nullable=True),
    sa.Column('commission_amount', sa.Numeric(precision=18, scale=2), nullable=False),
    sa.Column('proposed_rate', sa.Numeric(precision=18, scale=6), nullable=False),
    sa.Column('proposed_by_id', sa.Integer(), nullable=False),
    sa.Column('proposed_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('decision', sa.Enum('PENDING', 'ACCEPTED', 'REJECTED', name='payment_terms_decision'), nullable=False),
    sa.Column('decision_comment', sa.Text(), nullable=True),
    sa.Column('decided_by_id', sa.Integer(), nullable=True),
    sa.Column('decided_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['decided_by_id'], ['users.id'], name=op.f('fk_payment_terms_proposals_decided_by_id_users')),
    sa.ForeignKeyConstraint(['payment_request_id'], ['payment_requests.request_id'], name=op.f('fk_payment_terms_proposals_payment_request_id_payment_requests'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['proposed_agent_id'], ['agents.id'], name=op.f('fk_payment_terms_proposals_proposed_agent_id_agents')),
    sa.ForeignKeyConstraint(['proposed_by_id'], ['users.id'], name=op.f('fk_payment_terms_proposals_proposed_by_id_users')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_payment_terms_proposals'))
    )
    op.add_column('payment_requests', sa.Column('agreed_commission_amount', sa.Numeric(precision=18, scale=2), nullable=True))
    op.add_column('payment_requests', sa.Column('agreed_rate', sa.Numeric(precision=18, scale=6), nullable=True))
    op.alter_column('payment_requests', 'payment_method',
               existing_type=postgresql.ENUM('BANK', 'AGENT', name='payment_method'),
               nullable=True)
    op.add_column('requests', sa.Column('requester_id', sa.Integer(), nullable=True))
    op.execute("UPDATE requests SET requester_id = created_by_id WHERE requester_id IS NULL")
    op.alter_column('requests', 'requester_id', nullable=False)
    op.create_foreign_key(op.f('fk_requests_requester_id_users'), 'requests', 'users', ['requester_id'], ['id'])


def downgrade() -> None:
    op.drop_constraint(op.f('fk_requests_requester_id_users'), 'requests', type_='foreignkey')
    op.drop_column('requests', 'requester_id')
    op.alter_column('payment_requests', 'payment_method',
               existing_type=postgresql.ENUM('BANK', 'AGENT', name='payment_method'),
               nullable=False)
    op.drop_column('payment_requests', 'agreed_rate')
    op.drop_column('payment_requests', 'agreed_commission_amount')
    op.drop_table('payment_terms_proposals')
