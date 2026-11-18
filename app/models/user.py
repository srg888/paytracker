from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base_class import Base
from app.models.enums import USER_ROLE_ENUM, UserRole
from app.models.mixins import TimestampMixin


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    telegram_id: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True)
    role: Mapped[UserRole] = mapped_column(USER_ROLE_ENUM, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # Аутентификация по паролю (заменяет прежний выбор пользователя из списка
    # на /login — см. app/services/auth.py). NULL = пароль ещё не задан,
    # вход невозможен (не "пароль пустой" — пустых паролей не бывает).
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Защита от перебора пароля (app/services/auth.py: LOCKOUT_SCHEDULE).
    failed_login_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_requests: Mapped[list["Request"]] = relationship(
        "Request", foreign_keys="Request.created_by_id", back_populates="created_by"
    )
    requested_requests: Mapped[list["Request"]] = relationship(
        "Request", foreign_keys="Request.requester_id", back_populates="requester"
    )
    executed_requests: Mapped[list["Request"]] = relationship(
        "Request", foreign_keys="Request.executor_id", back_populates="executor"
    )