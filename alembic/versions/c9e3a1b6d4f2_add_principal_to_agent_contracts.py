"""add principal_id to agent_contracts

Revision ID: c9e3a1b6d4f2
Revises: b8d2f0a5c3e9
Create Date: 2026-11-06 10:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'c9e3a1b6d4f2'
down_revision: Union[str, Sequence[str], None] = 'b8d2f0a5c3e9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Принципал агентского договора — заказчик (users с ролью ZAKAZCHIK),
    в интересах которого действует агент.

    NULL допустим намеренно: у существующих договоров принципал не был указан,
    а у части агентов договор может быть рамочным без привязки к заказчику.
    Обороты и вознаграждения по-прежнему считаются в разрезе договора;
    принципал нужен для отчёта «агент → договоры → принципал».
    """
    op.add_column('agent_contracts', sa.Column('principal_id', sa.Integer(), nullable=True))
    op.create_foreign_key(
        op.f('fk_agent_contracts_principal_id_users'),
        'agent_contracts', 'users', ['principal_id'], ['id'],
    )
    op.create_index(
        op.f('ix_agent_contracts_principal_id'), 'agent_contracts', ['principal_id'], unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f('ix_agent_contracts_principal_id'), table_name='agent_contracts')
    op.drop_constraint(
        op.f('fk_agent_contracts_principal_id_users'), 'agent_contracts', type_='foreignkey',
    )
    op.drop_column('agent_contracts', 'principal_id')
