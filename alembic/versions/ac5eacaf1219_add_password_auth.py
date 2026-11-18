"""add password_hash, failed_login_count, locked_until to users;
add LOGIN_FAILED audit action value

Revision ID: ac5eacaf1219
Revises: c9e3a1b6d4f2
Create Date: 2026-11-05 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'ac5eacaf1219'
down_revision: Union[str, Sequence[str], None] = 'c9e3a1b6d4f2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Реальная аутентификация по логину/паролю вместо выбора пользователя
    из списка на /login (см. AGENTS.md — там это прямо названо MVP-заглушкой).
    password_hash nullable: до разового посева паролей (scripts/seed_passwords.py)
    существующие пользователи не смогут войти — это НАМЕРЕННО, а не баг:
    открытый доступ без пароля не должен оставаться доступным ни на секунду
    дольше необходимого на переходный период."""
    op.add_column('users', sa.Column('password_hash', sa.String(length=255), nullable=True))
    op.add_column('users', sa.Column('failed_login_count', sa.Integer(), nullable=False, server_default='0'))
    op.add_column('users', sa.Column('locked_until', sa.DateTime(timezone=True), nullable=True))
    op.execute("ALTER TYPE audit_action_type ADD VALUE IF NOT EXISTS 'LOGIN_FAILED'")


def downgrade() -> None:
    """Откат (значение enum не удаляем — append-only журнал)."""
    op.drop_column('users', 'locked_until')
    op.drop_column('users', 'failed_login_count')
    op.drop_column('users', 'password_hash')
