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
    TERMS_PROPOSED = "terms_proposed"
    MANAGER_REVIEW = "manager_review"
    CLOSED = "closed"


class PaymentMethod(str, enum.Enum):
    BANK = "bank"
    AGENT = "agent"


class PaymentTermsDecision(str, enum.Enum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"


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
    EXECUTOR_SELF_ASSIGNED = "executor_self_assigned"
    REJECTED = "rejected"
    ACKNOWLEDGED_REJECTION = "acknowledged_rejection"
    CONFIRMED_EXECUTION = "confirmed_execution"
    DOCUMENTS_CONFIRMED = "documents_confirmed"
    DELEGATION_STARTED = "delegation_started"
    DELEGATION_ENDED = "delegation_ended"
    DELEGATION_REVOKED = "delegation_revoked"
    LOGIN = "login"
    LOGOUT = "logout"
    TERMS_PROPOSED = "terms_proposed"
    TERMS_ACCEPTED = "terms_accepted"
    TERMS_REJECTED = "terms_rejected"
    SENT_FOR_MANAGER_REVIEW = "sent_for_manager_review"
    REWORK_REQUESTED = "rework_requested"
    CLOSED_BY_MANAGER = "closed_by_manager"
    CREATED_FOR_REQUESTER = "created_for_requester"
    COMMENT_ATTACHMENT_UPLOADED = "comment_attachment_uploaded"


USER_ROLE_ENUM = SAEnum(UserRole, name="user_role")
REQUEST_TYPE_ENUM = SAEnum(RequestType, name="request_type")
REQUEST_STATUS_ENUM = SAEnum(RequestStatus, name="request_status")
PAYMENT_METHOD_ENUM = SAEnum(PaymentMethod, name="payment_method")
PAYMENT_TERMS_DECISION_ENUM = SAEnum(PaymentTermsDecision, name="payment_terms_decision")
DOCUMENT_CATEGORY_ENUM = SAEnum(DocumentCategory, name="document_category")
AUDIT_ACTION_TYPE_ENUM = SAEnum(AuditActionType, name="audit_action_type")