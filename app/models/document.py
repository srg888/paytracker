from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base_class import Base


class RequestDocument(Base):
    """Загруженный файл. Документы загружаются один раз, по ходу исполнения заявки
    (см. Исполнитель исполняет заявку.md) — переиспользуются на этапе проверки
    комплектности, а не загружаются заново."""

    __tablename__ = "request_documents"

    id: Mapped[int] = mapped_column(primary_key=True)
    request_id: Mapped[int] = mapped_column(ForeignKey("requests.id", ondelete="CASCADE"), nullable=False)
    document_type_code: Mapped[str] = mapped_column(ForeignKey("document_types.code"), nullable=False)

    file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    storage_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    file_size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)

    uploaded_by_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    request: Mapped["Request"] = relationship(back_populates="documents")
    document_type: Mapped["DocumentType"] = relationship()
    uploaded_by: Mapped["User"] = relationship()


class RequestDocumentRequirement(Base):
    """Переопределение обязательности документа для конкретной заявки.
    По умолчанию обязательность берётся из DocumentType.is_required_default,
    но Руководитель может переопределить перечень обязательных документов
    по каждой заявке (см. Исполнитель готовит закрывающие документы.md)."""

    __tablename__ = "request_document_requirements"

    id: Mapped[int] = mapped_column(primary_key=True)
    request_id: Mapped[int] = mapped_column(ForeignKey("requests.id", ondelete="CASCADE"), nullable=False)
    document_type_code: Mapped[str] = mapped_column(ForeignKey("document_types.code"), nullable=False)
    is_required_override: Mapped[bool] = mapped_column(nullable=False)

    request: Mapped["Request"] = relationship(back_populates="document_requirements")
    document_type: Mapped["DocumentType"] = relationship()
