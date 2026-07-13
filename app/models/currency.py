from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import Boolean, Date, ForeignKey, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base_class import Base
from app.models.mixins import TimestampMixin


class Currency(Base):
    __tablename__ = "currencies"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(3), unique=True, nullable=False)  # ISO 4217: USD, EUR, ...

    exchange_rates: Mapped[list["ExchangeRate"]] = relationship(back_populates="currency")


class ExchangeRate(Base, TimestampMixin):
    """Кэш курсов ЦБ по датам — не запрашивать cbr.ru на каждое открытие заявки."""

    __tablename__ = "exchange_rates"
    __table_args__ = (
        UniqueConstraint("currency_id", "rate_date", name="uq_exchange_rate_currency_date"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    currency_id: Mapped[int] = mapped_column(ForeignKey("currencies.id"), nullable=False)
    rate_date: Mapped[date] = mapped_column(Date, nullable=False)
    rate_value: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    # true, если курс на эту дату не удалось обновить из cbr.ru и мы отдаём последний известный
    is_stale: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    currency: Mapped["Currency"] = relationship(back_populates="exchange_rates")
