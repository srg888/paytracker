from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models.audit_log import AuditLog
from app.models.enums import AuditActionType, RequestStatus, RequestType, UserRole
from app.models.request import Request
from app.models.status_history import RequestStatusHistory
from app.models.user import User
from app.services.documents import missing_required_documents
from app.services.roles import acting_rukovoditel_label, is_acting_rukovoditel


class TransitionError(Exception):
    pass


def _record(
    db: Session,
    req: Request,
    from_status: RequestStatus | None,
    to_status: RequestStatus,
    user: User,
    comment: str | None,
    action_type: AuditActionType,
) -> None:
    req.status = to_status
    if to_status == RequestStatus.CLOSED:
        req.closed_at = datetime.now(timezone.utc)

    delegated_note = acting_rukovoditel_label(db, user)
    full_comment = comment
    if delegated_note:
        full_comment = f"{comment} [{delegated_note}]" if comment else f"[{delegated_note}]"

    db.add(
        RequestStatusHistory(
            request_id=req.id,
            from_status=from_status,
            to_status=to_status,
            changed_by_id=user.id,
            comment=full_comment,
        )
    )
    db.add(
        AuditLog(
            entity_type="request",
            entity_id=req.id,
            user_id=user.id,
            action_type=action_type,
            field_name="status",
            old_value=from_status.value if from_status else None,
            new_value=to_status.value,
        )
    )


def submit(db: Session, req: Request, user: User) -> None:
    if req.created_by_id != user.id:
        raise TransitionError("Подать заявку может только её автор.")
    if req.status != RequestStatus.DRAFT:
        raise TransitionError("Заявка уже подана.")
    req.submitted_at = datetime.now(timezone.utc)
    _record(db, req, RequestStatus.DRAFT, RequestStatus.NEW, user, None, AuditActionType.STATUS_CHANGE)


def assign_executor(db: Session, req: Request, user: User, executor_id: int) -> None:
    if not is_acting_rukovoditel(db, user):
        raise TransitionError("Назначать исполнителя может только Руководитель.")
    if req.status not in (RequestStatus.NEW, RequestStatus.IN_PROGRESS):
        raise TransitionError("Заявка должна быть в статусе 'Новая заявка' или 'В работе'.")
    executor = db.get(User, executor_id)
    if not executor:
        raise TransitionError("Исполнитель не найден.")
    if executor.role not in (UserRole.ISPOLNITEL, UserRole.RUKOVODITEL):
        raise TransitionError("Назначить можно только Исполнителя или Руководителя.")
    if not executor.is_active:
        raise TransitionError("Нельзя назначить неактивного пользователя.")
    is_reassign = req.status == RequestStatus.IN_PROGRESS
    req.executor_id = executor_id
    if is_reassign:
        _record(
            db, req, RequestStatus.IN_PROGRESS, RequestStatus.IN_PROGRESS, user, f"Переназначен на {executor.full_name}",
            AuditActionType.EXECUTOR_ASSIGNED,
        )
    else:
        _record(
            db, req, RequestStatus.NEW, RequestStatus.IN_PROGRESS, user, None, AuditActionType.EXECUTOR_ASSIGNED
        )


def self_assign(db: Session, req: Request, user: User) -> None:
    """Новая заявка -> В работе. Исполнитель может взять заявку самостоятельно (BR-023)."""
    if user.role not in (UserRole.ISPOLNITEL, UserRole.RUKOVODITEL):
        raise TransitionError("Взять заявку может только Исполнитель или Руководитель.")
    if not user.is_active:
        raise TransitionError("Неактивный пользователь не может взять заявку.")
    if req.status != RequestStatus.NEW:
        raise TransitionError("Заявка не в статусе 'Новая заявка'.")
    req.executor_id = user.id
    _record(db, req, RequestStatus.NEW, RequestStatus.IN_PROGRESS, user, None, AuditActionType.EXECUTOR_SELF_ASSIGNED)


def reject(db: Session, req: Request, user: User, reason: str) -> None:
    if not is_acting_rukovoditel(db, user):
        raise TransitionError("Отклонить заявку может только Руководитель.")
    if req.status != RequestStatus.NEW:
        raise TransitionError("Заявка не в статусе 'Новая заявка'.")
    if not reason or not reason.strip():
        raise TransitionError("Укажите причину отклонения.")
    _record(db, req, RequestStatus.NEW, RequestStatus.REJECTED, user, reason, AuditActionType.REJECTED)


def acknowledge_rejection(db: Session, req: Request, user: User) -> None:
    if req.requester_id != user.id:
        raise TransitionError("Только автор заявки может подтвердить ознакомление.")
    if req.status != RequestStatus.REJECTED:
        raise TransitionError("Заявка не в статусе 'Отклонена'.")
    _record(
        db, req, RequestStatus.REJECTED, RequestStatus.ARCHIVED, user, None,
        AuditActionType.ACKNOWLEDGED_REJECTION,
    )


def request_clarification(db: Session, req: Request, user: User, question: str) -> None:
    """В работе -> Уточнение или Новая -> Уточнение (до назначения, BR-042)."""
    if user.role not in (UserRole.ISPOLNITEL, UserRole.RUKOVODITEL):
        raise TransitionError("Запросить уточнение может Исполнитель или Руководитель.")
    if req.status not in (RequestStatus.IN_PROGRESS, RequestStatus.NEW):
        raise TransitionError("Заявка не в статусе 'В работе' или 'Новая заявка'.")
    _record(
        db, req, req.status, RequestStatus.CLARIFICATION, user, question,
        AuditActionType.STATUS_CHANGE,
    )


def answer_clarification(db: Session, req: Request, user: User, answer: str) -> None:
    if req.requester_id != user.id:
        raise TransitionError("Ответить может только Заказчик заявки.")
    if req.status != RequestStatus.CLARIFICATION:
        raise TransitionError("Заявка не в статусе 'Уточнение'.")
    prev_status = RequestStatus.IN_PROGRESS
    for h in reversed(req.status_history):
        if h.to_status == RequestStatus.CLARIFICATION and h.from_status is not None:
            prev_status = h.from_status
            break
    _record(
        db, req, RequestStatus.CLARIFICATION, prev_status, user, answer,
        AuditActionType.STATUS_CHANGE,
    )


def propose_terms(db: Session, req: Request, user: User) -> None:
    if req.executor_id != user.id:
        raise TransitionError("Предложить условия может только назначенный Исполнитель.")
    if req.status != RequestStatus.IN_PROGRESS:
        raise TransitionError("Заявка не в статусе 'В работе'.")
    if not req.payment_details:
        raise TransitionError("Согласование условий только для платежей.")
    _record(
        db, req, RequestStatus.IN_PROGRESS, RequestStatus.TERMS_PROPOSED, user, None,
        AuditActionType.TERMS_PROPOSED,
    )


def accept_terms(db: Session, req: Request, user: User) -> None:
    if req.requester_id != user.id:
        raise TransitionError("Принять условия может только Заказчик заявки.")
    if req.status != RequestStatus.TERMS_PROPOSED:
        raise TransitionError("Заявка не в статусе 'Условия предложены'.")
    _record(
        db, req, RequestStatus.TERMS_PROPOSED, RequestStatus.IN_PROGRESS, user, None,
        AuditActionType.TERMS_ACCEPTED,
    )


def reject_terms(db: Session, req: Request, user: User, reason: str) -> None:
    if req.requester_id != user.id:
        raise TransitionError("Отклонить условия может только Заказчик заявки.")
    if req.status != RequestStatus.TERMS_PROPOSED:
        raise TransitionError("Заявка не в статусе 'Условия предложены'.")
    if not reason or not reason.strip():
        raise TransitionError("Укажите причину отклонения условий.")
    _record(
        db, req, RequestStatus.TERMS_PROPOSED, RequestStatus.IN_PROGRESS, user, reason,
        AuditActionType.TERMS_REJECTED,
    )


def mark_execution_done(db: Session, req: Request, user: User) -> None:
    if req.executor_id != user.id:
        raise TransitionError("Завершить исполнение может только назначенный Исполнитель.")
    if req.status != RequestStatus.IN_PROGRESS:
        raise TransitionError("Заявка не в статусе 'В работе'.")
    _record(
        db, req, RequestStatus.IN_PROGRESS, RequestStatus.AWAITING_CUSTOMER_CONFIRMATION, user, None,
        AuditActionType.STATUS_CHANGE,
    )


def confirm_execution(db: Session, req: Request, user: User) -> None:
    is_requester = req.requester_id == user.id
    acting_ruk = is_acting_rukovoditel(db, user)
    if not is_requester and not acting_ruk:
        raise TransitionError("Подтвердить исполнение может автор заявки или Руководитель.")
    if req.status != RequestStatus.AWAITING_CUSTOMER_CONFIRMATION:
        raise TransitionError("Заявка не в статусе 'Ожидает подтверждения Заказчика'.")
    _record(
        db, req, RequestStatus.AWAITING_CUSTOMER_CONFIRMATION, RequestStatus.DOCUMENT_CHECK, user, None,
        AuditActionType.CONFIRMED_EXECUTION,
    )


def confirm_documents_complete(db: Session, req: Request, user: User) -> None:
    """Проверка комплектности документов -> На проверке у Руководителя.
    Исполнитель отправляет комплект на проверку Руководителю (BR-110)."""
    if req.executor_id != user.id:
        raise TransitionError("Отправить на проверку может только назначенный Исполнитель.")
    if req.status != RequestStatus.DOCUMENT_CHECK:
        raise TransitionError("Заявка не в статусе 'Проверка комплектности документов'.")
    missing = missing_required_documents(db, req)
    if missing:
        names = ", ".join(f"{d.code} ({d.name})" for d in missing)
        raise TransitionError(f"Не хватает обязательных документов: {names}")
    _record(
        db, req, RequestStatus.DOCUMENT_CHECK, RequestStatus.MANAGER_REVIEW, user, None,
        AuditActionType.SENT_FOR_MANAGER_REVIEW,
    )


def manager_close(db: Session, req: Request, user: User) -> None:
    """На проверке у Руководителя -> Закрыта. Только Руководитель (BR-110, BR-111)."""
    if not is_acting_rukovoditel(db, user):
        raise TransitionError("Закрыть заявку может только Руководитель.")
    if req.status != RequestStatus.MANAGER_REVIEW:
        raise TransitionError("Заявка не в статусе 'На проверке у Руководителя'.")
    _record(
        db, req, RequestStatus.MANAGER_REVIEW, RequestStatus.CLOSED, user, None,
        AuditActionType.CLOSED_BY_MANAGER,
    )


def rework_from_manager(db: Session, req: Request, user: User, reason: str) -> None:
    """На проверке у Руководителя -> Проверка комплектности документов.
    Руководитель возвращает на доработку с комментарием (BR-111)."""
    if not is_acting_rukovoditel(db, user):
        raise TransitionError("Отправить на доработку может только Руководитель.")
    if req.status != RequestStatus.MANAGER_REVIEW:
        raise TransitionError("Заявка не в статусе 'На проверке у Руководителя'.")
    if not reason or not reason.strip():
        raise TransitionError("Укажите причину возврата на доработку.")
    _record(
        db, req, RequestStatus.MANAGER_REVIEW, RequestStatus.DOCUMENT_CHECK, user, reason,
        AuditActionType.REWORK_REQUESTED,
    )