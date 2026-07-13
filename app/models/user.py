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

    created_requests: Mapped[list["Request"]] = relationship(
        "Request", foreign_keys="Request.created_by_id", back_populates="created_by"
    )
    executed_requests: Mapped[list["Request"]] = relationship(
        "Request", foreign_keys="Request.executor_id", back_populates="executor"
    )
