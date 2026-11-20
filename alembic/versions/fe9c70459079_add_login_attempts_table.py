"""add login_attempts table (per account+IP lockout); drop
users.failed_login_count/locked_until (per-account-only lockout was a
design mistake — see discussion: it let a single known/guessed email be
locked out indefinitely from any IP)

Revision ID: fe9c70459079
Revises: ac5eacaf1219
Create Date: 2026-11-06 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'fe9c70459079'
down_revision: Union[str, Sequence[str], None] = 'ac5eacaf1219'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'login_attempts',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=True),
        sa.Column('ip', sa.String(length=64), nullable=True),
        sa.Column('success', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_login_attempts_user_id_users')),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_login_attempts')),
    )
    op.create_index(
        'ix_login_attempts_user_ip_created', 'login_attempts', ['user_id', 'ip', 'created_at'], unique=False
    )

    # Поля из ac5eacaf1219 были ошибкой дизайна (блокировка по аккаунту
    # целиком, без привязки к IP) — убираем, данных на проде по ним ещё
    # нет смысла сохранять (миграция только что применилась, значения
    # либо дефолт 0/NULL, либо несколько записей за время между деплоями).
    op.drop_column('users', 'locked_until')
    op.drop_column('users', 'failed_login_count')


def downgrade() -> None:
    op.add_column('users', sa.Column('failed_login_count', sa.Integer(), nullable=False, server_default='0'))
    op.add_column('users', sa.Column('locked_until', sa.DateTime(timezone=True), nullable=True))
    op.drop_index('ix_login_attempts_user_ip_created', table_name='login_attempts')
    op.drop_table('login_attempts')
