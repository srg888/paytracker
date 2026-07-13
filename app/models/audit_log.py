from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base_class import Base
from app.models.enums import AUDIT_ACTION_TYPE_ENUM, AuditActionType


class AuditLog(Base):
    """Append-only журнал. Не редактируется и не удаляется из кода приложения —
    на уровне БД это можно дополнительно закрепить REVOKE UPDATE/DELETE для
    роли приложения (см. аудит.md), сюда это не включено, т.к. это не касается
    структуры таблиц, а прав доступа на уровне БД."""

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    entity_type: Mapped[str] = mapped_column(String(64), nullable=False)  # напр. "request", "delegation"
    entity_id: Mapped[int] = mapped_column(nullable=False)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    action_type: Mapped[AuditActionType] = mapped_column(AUDIT_ACTION_TYPE_ENUM, nullable=False)
    field_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    old_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    new_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    user: Mapped["User"] = relationship()
