from __future__ import annotations

from sqlalchemy import Boolean, String
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
    # Блокировка после перебора пароля считается по app.models.login_attempt
    # .LoginAttempt (пара аккаунт+IP), не хранится здесь — см. правку после
    # разбора DeepSeek: блокировка по аккаунту целиком позволяла держать
    # единственного Руководителя без доступа бесконечно с любого IP.

    created_requests: Mapped[list["Request"]] = relationship(
        "Request", foreign_keys="Request.created_by_id", back_populates="created_by"
    )
    requested_requests: Mapped[list["Request"]] = relationship(
        "Request", foreign_keys="Request.requester_id", back_populates="requester"
    )
    executed_requests: Mapped[list["Request"]] = relationship(
        "Request", foreign_keys="Request.executor_id", back_populates="executor"
    )