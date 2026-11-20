"""Аутентификация по паролю (заменяет прежний выбор пользователя из списка
на /login — см. AGENTS.md, где это прямо названо MVP-заглушкой) и защита
от перебора пароля.

Хеширование — argon2id (argon2-cffi), текущая рекомендация OWASP для новых
систем (устойчивее bcrypt к атакам на GPU/ASIC за счёт требовательности
к памяти, не только к процессору).

Блокировка после перебора — по паре (аккаунт, IP), не по аккаунту целиком.
Это сознательная правка после разбора: блокировка только по аккаунту
позволяла держать ЕДИНСТВЕННОГО Руководителя без доступа сколько угодно
долго — зная (или угадав по шаблону) его email, с любого IP, просто
повторяя по одной неверной попытке сразу после истечения очередной
блокировки. По паре аккаунт+IP: атакующий блокирует сам себя для этой
пары, а настоящий Руководитель, заходя с другого IP, атаки не замечает.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, InvalidHash
from sqlalchemy.orm import Session

from app.models.login_attempt import LoginAttempt

_hasher = PasswordHasher()

# Первые 4 неудачные попытки подряд (с одного IP, на один аккаунт) —
# без последствий (ошибся раскладкой — попробовал снова), дальше задержка
# растёт по расписанию.
LOCKOUT_FREE_ATTEMPTS = 4
LOCKOUT_SCHEDULE_MINUTES = [1, 5, 30, 60, 240]


def hash_password(plain: str) -> str:
    return _hasher.hash(plain)


def verify_password(plain: str, password_hash: str) -> bool:
    try:
        return _hasher.verify(password_hash, plain)
    except (VerifyMismatchError, InvalidHash):
        return False


def record_attempt(db: Session, user_id: int | None, ip: str | None, success: bool) -> None:
    db.add(LoginAttempt(user_id=user_id, ip=ip, success=success))


def _consecutive_failures(db: Session, user_id: int, ip: str | None) -> int:
    """Число неудачных попыток для этой пары (аккаунт, IP) подряд с момента
    последнего успешного входа с этого же IP (или с начала времён, если
    успешных входов с этого IP ещё не было)."""
    last_success = (
        db.query(LoginAttempt.created_at)
        .filter(LoginAttempt.user_id == user_id, LoginAttempt.ip == ip, LoginAttempt.success.is_(True))
        .order_by(LoginAttempt.created_at.desc())
        .first()
    )
    q = db.query(LoginAttempt).filter(
        LoginAttempt.user_id == user_id, LoginAttempt.ip == ip, LoginAttempt.success.is_(False)
    )
    if last_success:
        q = q.filter(LoginAttempt.created_at > last_success[0])
    return q.count()


def lockout_status(db: Session, user_id: int, ip: str | None) -> tuple[bool, int]:
    """(заблокирован?, сколько секунд осталось) для пары (аккаунт, IP).
    Ничего не хранит отдельно — считается каждый раз по журналу попыток,
    в духе остального аудита в проекте (append-only, не мутируемый счётчик)."""
    failures = _consecutive_failures(db, user_id, ip)
    over = failures - LOCKOUT_FREE_ATTEMPTS
    if over <= 0:
        return False, 0
    last_failed = (
        db.query(LoginAttempt.created_at)
        .filter(LoginAttempt.user_id == user_id, LoginAttempt.ip == ip, LoginAttempt.success.is_(False))
        .order_by(LoginAttempt.created_at.desc())
        .first()
    )
    if not last_failed:
        return False, 0
    idx = min(over - 1, len(LOCKOUT_SCHEDULE_MINUTES) - 1)
    unlock_at = last_failed[0] + timedelta(minutes=LOCKOUT_SCHEDULE_MINUTES[idx])
    now = datetime.now(timezone.utc)
    if now < unlock_at:
        return True, int((unlock_at - now).total_seconds())
    return False, 0
