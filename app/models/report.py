from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base_class import Base
from app.models.enums import ExportFormat


class Report(Base):
    """Журнал генерируемых отчётов (экспорта данных по заявкам)."""

    __tablename__ = "reports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # NULL = отчёт по списку/фильтру, число = отчёт по конкретной заявке
    request_id: Mapped[int | None] = mapped_column(ForeignKey("requests.id", ondelete="CASCADE"), nullable=True)
    export_format: Mapped[str] = mapped_column(String(10), nullable=False)
    file_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    file_size_bytes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_by_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    request: Mapped["Request"] = relationship()
    creator: Mapped["User"] = relationship()
