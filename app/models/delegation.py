from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import CheckConstraint, Date, DateTime, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base_class import Base


class Delegation(Base):
    """Временная передача полномочий Руководителя одному из Исполнителей.
    Одновременно активной должна быть только одна делегация — это правило
    проверяется на уровне приложения (см. PayTracker.md), не БД, так как
    "активна ли делегация" зависит от текущей даты, а не только от полей записи."""

    __tablename__ = "delegations"
    __table_args__ = (
        CheckConstraint("start_date <= end_date", name="ck_delegation_start_before_end"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    delegator_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    delegate_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    delegator: Mapped["User"] = relationship(foreign_keys=[delegator_id])
    delegate: Mapped["User"] = relationship(foreign_keys=[delegate_id])
