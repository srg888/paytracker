from sqlalchemy.orm import Session

from app.models.document import RequestDocument, RequestDocumentRequirement
from app.models.document_type import DocumentType
from app.models.enums import DocumentCategory, PaymentMethod, RequestType
from app.models.request import Request


def document_category_for_request(req: Request) -> DocumentCategory | None:
    if req.type == RequestType.PURCHASE:
        return DocumentCategory.PURCHASE
    if req.type == RequestType.PAYMENT and req.payment_details:
        if req.payment_details.payment_method == PaymentMethod.AGENT:
            return DocumentCategory.PAYMENT_AGENT
        return DocumentCategory.PAYMENT_BANK
    return None  # консультация — комплект документов не требуется


def missing_required_documents(db: Session, req: Request) -> list[DocumentType]:
    """Возвращает список ОТСУТСТВУЮЩИХ обязательных типов документов,
    с учётом переопределений в request_document_requirements (см.
    Проверка комплектности и закрытие заявки.md) и маршрута исполнения
    (BR-122): при способе "через агента" часть документов обязательна
    только если маршрут включает Субагента."""
    category = document_category_for_request(req)
    if category is None:
        return []

    all_types = db.query(DocumentType).filter(DocumentType.category == category).all()
    overrides = {
        o.document_type_code: o.is_required_override
        for o in db.query(RequestDocumentRequirement).filter(
            RequestDocumentRequirement.request_id == req.id
        )
    }
    uploaded_codes = {
        d.document_type_code
        for d in db.query(RequestDocument).filter(RequestDocument.request_id == req.id)
    }

    missing = []
    for dt in all_types:
        is_required = overrides.get(dt.code, dt.is_required_default)
        if is_required and dt.code not in uploaded_codes and _is_required_for_route(dt, req):
            missing.append(dt)
    return missing


def available_document_types(db: Session, req: Request) -> list[DocumentType]:
    """Все типы документов, применимые к заявке (с учётом маршрута).
    Для формы загрузки показываем и необязательные, и условные (если маршрут
    включает субагента)."""
    category = document_category_for_request(req)
    if category is None:
        return []
    return [
        dt for dt in db.query(DocumentType).filter(DocumentType.category == category).all()
        if _applicable_for_route(dt, req)
    ]


_SUBAGENT_ONLY_CODES = {"ПА-03", "ПА-05", "ПА-06", "ПА-09", "ПА-10"}


def _is_required_for_route(dt: DocumentType, req: Request) -> bool:
    """Условная обязательность по маршруту исполнения платежа (BR-122).

    Коды ПА-03/05/06/09/10 ("Поручение Агент–Субагент", "ПП на Субагента",
    "СВИФТ от Субагента", "Акт-отчёт Субагент–Агент") обязательны ТОЛЬКО
    когда маршрут включает Субагента. Для банковского прямого платежа
    документы категории "Платёж через агента" не нужны вовсе.
    """
    if not _applicable_for_route(dt, req):
        return False
    return True  # остальное решает is_required_default / overrides


def _applicable_for_route(dt: DocumentType, req: Request) -> bool:
    """Применим ли тип документа к данной заявке с учётом маршрута."""
    if dt.category == DocumentCategory.PAYMENT_AGENT:
        if not req.payment_details or req.payment_details.payment_method != PaymentMethod.AGENT:
            return False
        if dt.code in _SUBAGENT_ONLY_CODES and req.payment_details.subagent_id is None:
            return False
        return True
    if dt.category == DocumentCategory.PAYMENT_BANK:
        if not req.payment_details or req.payment_details.payment_method != PaymentMethod.BANK:
            return False
        return True
    return True  # PURCHASE — все типы применимы
