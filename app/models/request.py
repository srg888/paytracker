from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Date, DateTime, ForeignKey, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base_class import Base
from app.models.enums import (
    PAYMENT_METHOD_ENUM,
    REQUEST_STATUS_ENUM,
    REQUEST_TYPE_ENUM,
    PaymentMethod,
    RequestStatus,
    RequestType,
)
from app.models.mixins import TimestampMixin


class Request(Base, TimestampMixin):
    """Базовая таблица заявки. Общие для всех типов поля — здесь.
    Специфичные под тип поля — в PaymentRequest / PurchaseRequest / ConsultationRequest
    (паттерн "таблица на тип", чтобы не городить одну таблицу с кучей NULL-колонок)."""

    __tablename__ = "requests"

    id: Mapped[int] = mapped_column(primary_key=True)
    number: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    type: Mapped[RequestType] = mapped_column(REQUEST_TYPE_ENUM, nullable=False)
    status: Mapped[RequestStatus] = mapped_column(
        REQUEST_STATUS_ENUM, nullable=False, default=RequestStatus.DRAFT
    )

    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    expected_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    division_id: Mapped[int] = mapped_column(ForeignKey("divisions.id"), nullable=False)
    created_by_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    executor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)

    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    division: Mapped["Division"] = relationship()
    created_by: Mapped["User"] = relationship(foreign_keys=[created_by_id], back_populates="created_requests")
    executor: Mapped["User | None"] = relationship(foreign_keys=[executor_id], back_populates="executed_requests")

    payment_details: Mapped["PaymentRequest | None"] = relationship(
        back_populates="request", uselist=False, cascade="all, delete-orphan"
    )
    purchase_details: Mapped["PurchaseRequest | None"] = relationship(
        back_populates="request", uselist=False, cascade="all, delete-orphan"
    )
    consultation_details: Mapped["ConsultationRequest | None"] = relationship(
        back_populates="request", uselist=False, cascade="all, delete-orphan"
    )

    status_history: Mapped[list["RequestStatusHistory"]] = relationship(
        back_populates="request", cascade="all, delete-orphan", order_by="RequestStatusHistory.changed_at"
    )
    comments: Mapped[list["RequestComment"]] = relationship(
        back_populates="request", cascade="all, delete-orphan", order_by="RequestComment.created_at"
    )
    documents: Mapped[list["RequestDocument"]] = relationship(
        back_populates="request", cascade="all, delete-orphan"
    )
    document_requirements: Mapped[list["RequestDocumentRequirement"]] = relationship(
        back_populates="request", cascade="all, delete-orphan"
    )


class PaymentRequest(Base):
    """Специфичные поля для заявки типа 'платёж' — включая закупку, оплаченную
    через банк/агента, см. заявки на платежи.md."""

    __tablename__ = "payment_requests"

    request_id: Mapped[int] = mapped_column(
        ForeignKey("requests.id", ondelete="CASCADE"), primary_key=True
    )

    purpose: Mapped[str] = mapped_column(Text, nullable=False)  # Описание / цель платежа
    payment_purpose: Mapped[str] = mapped_column(Text, nullable=False)  # Назначение платежа
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    currency_id: Mapped[int] = mapped_column(ForeignKey("currencies.id"), nullable=False)

    # Реквизиты получателя
    recipient_name: Mapped[str] = mapped_column(String(255), nullable=False)
    recipient_country: Mapped[str] = mapped_column(String(128), nullable=False)
    recipient_address: Mapped[str] = mapped_column(String(255), nullable=False)
    recipient_bank: Mapped[str] = mapped_column(String(255), nullable=False)
    account_number_iban: Mapped[str] = mapped_column(String(64), nullable=False)
    swift_bic: Mapped[str] = mapped_column(String(16), nullable=False)
    additional_payment_info: Mapped[str | None] = mapped_column(Text, nullable=True)

    payment_method: Mapped[PaymentMethod] = mapped_column(PAYMENT_METHOD_ENUM, nullable=False)
    agent_id: Mapped[int | None] = mapped_column(ForeignKey("agents.id"), nullable=True)

    # Курс ЦБ: на дату заявки (инфо) и на дату фактического исполнения (используется в отчётах)
    rate_at_request: Mapped[Decimal | None] = mapped_column(Numeric(18, 6), nullable=True)
    amount_rub_at_request: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    rate_at_execution: Mapped[Decimal | None] = mapped_column(Numeric(18, 6), nullable=True)
    amount_rub_at_execution: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)

    request: Mapped["Request"] = relationship(back_populates="payment_details")
    currency: Mapped["Currency"] = relationship()
    agent: Mapped["Agent | None"] = relationship()


class PurchaseRequest(Base):
    """Специфичные поля для заявки типа 'закупка', см. Исполнитель исполняет заявку.md."""

    __tablename__ = "purchase_requests"

    request_id: Mapped[int] = mapped_column(
        ForeignKey("requests.id", ondelete="CASCADE"), primary_key=True
    )

    buyer_company_id: Mapped[int] = mapped_column(ForeignKey("buyer_companies.id"), nullable=False)
    payment_method: Mapped[PaymentMethod] = mapped_column(PAYMENT_METHOD_ENUM, nullable=False)
    markup_notes: Mapped[str | None] = mapped_column(Text, nullable=True)  # расчёт наценки, комментарий
    delivery_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    request: Mapped["Request"] = relationship(back_populates="purchase_details")
    buyer_company: Mapped["BuyerCompany"] = relationship()


class ConsultationRequest(Base):
    """Специфичные поля для заявки типа 'консультация', см. заявки консультации.md."""

    __tablename__ = "consultation_requests"

    request_id: Mapped[int] = mapped_column(
        ForeignKey("requests.id", ondelete="CASCADE"), primary_key=True
    )
    question_description: Mapped[str] = mapped_column(Text, nullable=False)

    request: Mapped["Request"] = relationship(back_populates="consultation_details")
