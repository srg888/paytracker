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
    Проверка комплектности и закрытие заявки.md)."""
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
        if is_required and dt.code not in uploaded_codes:
            missing.append(dt)
    return missing


def available_document_types(db: Session, req: Request) -> list[DocumentType]:
    category = document_category_for_request(req)
    if category is None:
        return []
    return db.query(DocumentType).filter(DocumentType.category == category).all()
