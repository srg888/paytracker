from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Numeric, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base_class import Base
from app.models.enums import (
    PAYMENT_METHOD_ENUM,
    PAYMENT_TERMS_DECISION_ENUM,
    PaymentMethod,
    PaymentTermsDecision,
)


class PaymentTermsProposal(Base):
    __tablename__ = "payment_terms_proposals"

    id: Mapped[int] = mapped_column(primary_key=True)
    payment_request_id: Mapped[int] = mapped_column(
        ForeignKey("payment_requests.request_id", ondelete="CASCADE"), nullable=False
    )

    proposed_payment_method: Mapped[PaymentMethod] = mapped_column(PAYMENT_METHOD_ENUM, nullable=False)
    proposed_agent_id: Mapped[int | None] = mapped_column(ForeignKey("agents.id"), nullable=True)
    commission_amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    proposed_rate: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)

    proposed_by_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    proposed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    decision: Mapped[PaymentTermsDecision] = mapped_column(
        PAYMENT_TERMS_DECISION_ENUM, nullable=False, default=PaymentTermsDecision.PENDING
    )
    decision_comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    decided_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    payment_request: Mapped["PaymentRequest"] = relationship(back_populates="terms_proposals")
    proposed_agent: Mapped["Agent | None"] = relationship()
    proposed_by: Mapped["User"] = relationship(foreign_keys=[proposed_by_id])
    decided_by: Mapped["User | None"] = relationship(foreign_keys=[decided_by_id])