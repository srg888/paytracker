import enum

from sqlalchemy import Enum as SAEnum


class UserRole(str, enum.Enum):
    RUKOVODITEL = "rukovoditel"
    ISPOLNITEL = "ispolnitel"
    ZAKAZCHIK = "zakazchik"


class RequestType(str, enum.Enum):
    PAYMENT = "payment"
    PURCHASE = "purchase"
    CONSULTATION = "consultation"


class RequestStatus(str, enum.Enum):
    DRAFT = "draft"
    NEW = "new"
    IN_PROGRESS = "in_progress"
    CLARIFICATION = "clarification"
    REJECTED = "rejected"
    ARCHIVED = "archived"
    AWAITING_CUSTOMER_CONFIRMATION = "awaiting_customer_confirmation"
    DOCUMENT_CHECK = "document_check"
    CLOSED = "closed"


class PaymentMethod(str, enum.Enum):
    BANK = "bank"
    AGENT = "agent"


class DocumentCategory(str, enum.Enum):
    PURCHASE = "purchase"
    PAYMENT_AGENT = "payment_agent"
    PAYMENT_BANK = "payment_bank"


class AuditActionType(str, enum.Enum):
    STATUS_CHANGE = "status_change"
    FIELD_CHANGE = "field_change"
    FILE_UPLOAD = "file_upload"
    FILE_DELETE = "file_delete"
    EXECUTOR_ASSIGNED = "executor_assigned"
    REJECTED = "rejected"
    ACKNOWLEDGED_REJECTION = "acknowledged_rejection"
    CONFIRMED_EXECUTION = "confirmed_execution"
    DOCUMENTS_CONFIRMED = "documents_confirmed"
    DELEGATION_STARTED = "delegation_started"
    DELEGATION_ENDED = "delegation_ended"
    DELEGATION_REVOKED = "delegation_revoked"
    LOGIN = "login"
    LOGOUT = "logout"


# Важно: создаём каждый Enum-тип ОДИН РАЗ и переиспользуем этот же объект во всех
# моделях, где он нужен (например payment_method используется и в PaymentRequest,
# и в PurchaseRequest). Если создавать `SAEnum(PaymentMethod, name="payment_method")`
# отдельно в каждом файле — SQLAlchemy будет считать это разными типами и Alembic
# autogenerate предложит лишние миграции/конфликт имён в PostgreSQL.
USER_ROLE_ENUM = SAEnum(UserRole, name="user_role")
REQUEST_TYPE_ENUM = SAEnum(RequestType, name="request_type")
REQUEST_STATUS_ENUM = SAEnum(RequestStatus, name="request_status")
PAYMENT_METHOD_ENUM = SAEnum(PaymentMethod, name="payment_method")
DOCUMENT_CATEGORY_ENUM = SAEnum(DocumentCategory, name="document_category")
AUDIT_ACTION_TYPE_ENUM = SAEnum(AuditActionType, name="audit_action_type")
