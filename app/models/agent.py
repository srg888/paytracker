from sqlalchemy import Boolean, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base
from app.models.mixins import TimestampMixin


class Agent(Base, TimestampMixin):
    """Справочник агентов. Поля намеренно минимальны для первой версии —
    полное расширение (банковские реквизиты, резидентство, история условий
    работы) запланировано отдельно, см. agents.md в vault."""

    __tablename__ = "agents"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_resident: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
