from fastapi import Request
from sqlalchemy.orm import Session

from app.models.user import User


def get_current_user(request: Request, db: Session) -> User | None:
    user_id = request.session.get("user_id")
    if not user_id:
        return None
    return db.get(User, user_id)


def flash(request: Request, message: str, level: str = "info") -> None:
    messages = request.session.get("flash", [])
    messages.append({"message": message, "level": level})
    request.session["flash"] = messages


def pop_flash(request: Request) -> list[dict]:
    messages = request.session.pop("flash", [])
    return messages
