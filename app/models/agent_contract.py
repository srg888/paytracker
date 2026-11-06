from __future__ import annotations

from datetime import date

from sqlalchemy import Boolean, Date, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base_class import Base
from app.models.mixins import TimestampMixin


class AgentContract(Base, TimestampMixin):
    """Договор с платёжным агентом. У одного агента может быть несколько
    договоров (последовательных или параллельных) — учёт оборотов,
    вознаграждений и порог постановки на валютный контроль (БР-131) ведутся
    в разрезе КОНКРЕТНОГО договора, а не агента в целом: порог 173-ФЗ привязан
    к сумме обязательств по контракту, а не к контрагенту."""

    __tablename__ = "agent_contracts"

    id: Mapped[int] = mapped_column(primary_key=True)
    agent_id: Mapped[int] = mapped_column(ForeignKey("agents.id"), nullable=False)
    number: Mapped[str] = mapped_column(String(100), nullable=False)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)  # NULL = бессрочный/действующий
    # БР-131: поставлен ли этот договор на валютный контроль. Пока false — при
    # достижении накопленных вознаграждений порогов 2.8/2.9 млн ₽ показывается
    # навязчивый баннер (см. app/services/agent_analytics.py).
    currency_control_registered: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Принципал — заказчик, в интересах которого действует агент. Заявки
    # привязаны к заказчику через requester_id, поэтому обороты по договору
    # считаются в разрезе «агент × принципал × договор».
    principal_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True, index=True
    )

    agent: Mapped["Agent"] = relationship(back_populates="contracts")
    principal: Mapped["User | None"] = relationship(foreign_keys=[principal_id])

    def is_active_on(self, on_date: date) -> bool:
        if on_date < self.start_date:
            return False
        if self.end_date is not None and on_date > self.end_date:
            return False
        return True
