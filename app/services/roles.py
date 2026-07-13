from datetime import date

from sqlalchemy.orm import Session

from app.models.delegation import Delegation
from app.models.enums import UserRole
from app.models.user import User


def is_acting_rukovoditel(db: Session, user: User) -> bool:
    """Пользователь действует как Руководитель, если он им является,
    либо является активным делегатом (см. PayTracker.md, раздел "Делегирование")."""
    if user.role == UserRole.RUKOVODITEL:
        return True
    today = date.today()
    active_delegation = (
        db.query(Delegation)
        .filter(
            Delegation.delegate_id == user.id,
            Delegation.start_date <= today,
            Delegation.end_date >= today,
            Delegation.revoked_at.is_(None),
        )
        .first()
    )
    return active_delegation is not None


def acting_rukovoditel_label(db: Session, user: User) -> str | None:
    """Если действие совершено делегатом, а не самим Руководителем — возвращает
    пометку для аудита, как описано в PayTracker.md."""
    if user.role == UserRole.RUKOVODITEL:
        return None
    today = date.today()
    active_delegation = (
        db.query(Delegation)
        .filter(
            Delegation.delegate_id == user.id,
            Delegation.start_date <= today,
            Delegation.end_date >= today,
            Delegation.revoked_at.is_(None),
        )
        .first()
    )
    if active_delegation:
        return "от имени Руководителя (делегировано)"
    return None
