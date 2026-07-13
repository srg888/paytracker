from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base_class import Base
from app.models.enums import REQUEST_STATUS_ENUM, RequestStatus


class RequestStatusHistory(Base):
    """Append-only лог переходов статусов. Используется и для отдельной таблицы
    быстрых выборок по SLA, и как часть общего аудита (см. аудит.md)."""

    __tablename__ = "request_status_history"

    id: Mapped[int] = mapped_column(primary_key=True)
    request_id: Mapped[int] = mapped_column(ForeignKey("requests.id", ondelete="CASCADE"), nullable=False)
    from_status: Mapped[RequestStatus | None] = mapped_column(REQUEST_STATUS_ENUM, nullable=True)
    to_status: Mapped[RequestStatus] = mapped_column(REQUEST_STATUS_ENUM, nullable=False)
    changed_by_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    request: Mapped["Request"] = relationship(back_populates="status_history")
    changed_by: Mapped["User"] = relationship()
