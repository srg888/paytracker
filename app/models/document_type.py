from sqlalchemy import Boolean, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base
from app.models.enums import DOCUMENT_CATEGORY_ENUM, DocumentCategory


class DocumentType(Base):
    """Единый справочник типов документов (коды из справочник типов документов.md,
    например ЗКП-01, ПА-01 и т.п.). Первичный ключ — сам код, он же используется
    как человекочитаемый идентификатор в UI и отчётах."""

    __tablename__ = "document_types"

    code: Mapped[str] = mapped_column(String(16), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    category: Mapped[DocumentCategory] = mapped_column(DOCUMENT_CATEGORY_ENUM, nullable=False)
    is_required_default: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
