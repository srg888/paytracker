from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base


class LoginAttempt(Base):
    """Append-only журнал попыток входа — используется для блокировки по
    паре (аккаунт, IP), а не по аккаунту целиком (см. обсуждение: блокировка
    только по аккаунту позволяла держать единственного Руководителя без
    доступа бесконечно, просто зная/угадав его email, с любого IP)."""

    __tablename__ = "login_attempts"

    id: Mapped[int] = mapped_column(primary_key=True)
    # NULL, если email не совпал ни с одним пользователем — попытка всё равно
    # логируется (видно, что кто-то подбирает несуществующие email), но не
    # участвует в блокировке конкретного аккаунта, т.к. аккаунта нет.
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    success: Mapped[bool] = mapped_column(Boolean, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        Index("ix_login_attempts_user_ip_created", "user_id", "ip", "created_at"),
    )
