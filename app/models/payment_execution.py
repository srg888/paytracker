from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Date, DateTime, ForeignKey, Numeric, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base_class import Base
from app.models.enums import PAYMENT_METHOD_ENUM, PaymentMethod


class PaymentExecution(Base):
    """Фактическое исполнение платёжной заявки (BR-080/081, ADR-006).

    Отдельная сущность от заявки-намерения: описывает реально совершённую
    операцию (дата, фактическая сумма, валюта, курс, сумма в рублях, способ,
    идентификатор операции, комментарий). Сейчас принят вариант A — одна заявка
    = одно исполнение; добавление варианта B (частичные исполнения) позже
    не потребует изменения этой модели (1:N уже заложена)."""

    __tablename__ = "payment_executions"

    id: Mapped[int] = mapped_column(primary_key=True)
    payment_request_id: Mapped[int] = mapped_column(
        ForeignKey("payment_requests.request_id", ondelete="CASCADE"), nullable=False
    )

    executed_at: Mapped[date] = mapped_column(Date, nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    currency_id: Mapped[int] = mapped_column(ForeignKey("currencies.id"), nullable=False)
    # Курс ЦБ и сумма в рублях фиксируются в момент подтверждения исполнения
    # Заказчиком (BR-061/062/091) — не пересчитываются позже.
    rate: Mapped[Decimal | None] = mapped_column(Numeric(18, 6), nullable=True)
    amount_rub: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)

    method: Mapped[PaymentMethod | None] = mapped_column(PAYMENT_METHOD_ENUM, nullable=True)
    operation_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_by_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    payment_request: Mapped["PaymentRequest"] = relationship(back_populates="executions")
    currency: Mapped["Currency"] = relationship()
    created_by: Mapped["User"] = relationship()