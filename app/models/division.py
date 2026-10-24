from sqlalchemy import Boolean, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base


class Division(Base):
    """Подразделение / юрлицо заказчика."""

    __tablename__ = "divisions"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    # Вывод из оборота: запись остаётся в истории заявок, но не предлагается
    # в выпадающих списках при создании новых (см. «Администрирование справочников»).
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
