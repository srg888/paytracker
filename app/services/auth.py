"""Аутентификация по паролю (заменяет прежний выбор пользователя из списка
на /login — см. AGENTS.md, где это прямо названо MVP-заглушкой) и защита
от перебора пароля.

Хеширование — argon2id (argon2-cffi), текущая рекомендация OWASP для новых
систем (устойчивее bcrypt к атакам на GPU/ASIC за счёт требовательности
к памяти, не только к процессору).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, InvalidHash
from sqlalchemy.orm import Session

from app.models.user import User

_hasher = PasswordHasher()

# Нарастающая блокировка после подряд идущих неудачных попыток: индекс —
# (failed_login_count - LOCKOUT_FREE_ATTEMPTS), значение — минуты блокировки.
# Первые 4 неудачные попытки блокировки не дают (ошибся — попробовал снова),
# дальше задержка растёт, чтобы перебор пароля стал практически бессмысленным,
# но не наказывал человека, который просто пару раз ошибся в раскладке.
LOCKOUT_FREE_ATTEMPTS = 4
LOCKOUT_SCHEDULE_MINUTES = [1, 5, 30, 60, 240]  # после 5-й, 6-й, 7-й... неудачи подряд


def hash_password(plain: str) -> str:
    return _hasher.hash(plain)


def verify_password(plain: str, password_hash: str) -> bool:
    try:
        return _hasher.verify(password_hash, plain)
    except (VerifyMismatchError, InvalidHash):
        return False


def is_locked(user: User) -> bool:
    return bool(user.locked_until and user.locked_until > datetime.now(timezone.utc))


def lockout_remaining_seconds(user: User) -> int:
    if not user.locked_until:
        return 0
    delta = user.locked_until - datetime.now(timezone.utc)
    return max(0, int(delta.total_seconds()))


def record_failed_attempt(db: Session, user: User) -> None:
    """Увеличивает счётчик неудачных попыток и, если он превысил
    LOCKOUT_FREE_ATTEMPTS, выставляет locked_until по LOCKOUT_SCHEDULE_MINUTES.
    Коммит — на вызывающей стороне (вместе с записью в AuditLog LOGIN_FAILED)."""
    user.failed_login_count += 1
    over = user.failed_login_count - LOCKOUT_FREE_ATTEMPTS
    if over > 0:
        idx = min(over - 1, len(LOCKOUT_SCHEDULE_MINUTES) - 1)
        minutes = LOCKOUT_SCHEDULE_MINUTES[idx]
        user.locked_until = datetime.now(timezone.utc) + timedelta(minutes=minutes)


def record_successful_login(user: User) -> None:
    user.failed_login_count = 0
    user.locked_until = None
