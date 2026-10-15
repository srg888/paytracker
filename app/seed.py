from sqlalchemy.orm import Session

from app.models.agent import Agent
from app.models.buyer_company import BuyerCompany
from app.models.currency import Currency
from app.models.division import Division
from app.models.document_type import DocumentType
from app.models.enums import DocumentCategory, UserRole
from app.models.user import User

CURRENCIES = ["USD", "EUR", "CNY", "RUB", "AED", "TRY", "KZT", "KGS", "GBP"]

DIVISIONS = ["Дирекция закупок", "Дирекция логистики", "Казначейство"]

AGENTS = ["ТиР", "Али", "А", "Эп"]

BUYER_COMPANIES = ["М", "BC", "Al", "Аз", "Az", "HK", "MT"]

DOCUMENT_TYPES = [
    # код, название, категория, обязателен по умолчанию, служба-получатель (BR-123)
    ("ЗКП-01", "Договор Заказчик–компания-покупатель", DocumentCategory.PURCHASE, True, "Юридический отдел"),
    ("ЗКП-02", "Инвойс Заказчик–компания-покупатель", DocumentCategory.PURCHASE, True, "Бухгалтерия"),
    ("ЗКП-03", "Спецификация/заказ Заказчик–компания-покупатель", DocumentCategory.PURCHASE, False, "Бухгалтерия"),
    ("ЗПС-01", "Договор компания-покупатель–Поставщик", DocumentCategory.PURCHASE, True, "Юридический отдел"),
    ("ЗПС-02", "Инвойс компания-покупатель–Поставщик", DocumentCategory.PURCHASE, True, "Бухгалтерия"),
    ("ЗПС-03", "Спецификация/заказ компания-покупатель–Поставщик", DocumentCategory.PURCHASE, False, "Бухгалтерия"),
    ("ТРН-01", "Транспортный документ (CMR/BL/AWB)", DocumentCategory.PURCHASE, True, "Бухгалтерия"),
    ("ТАМ-01", "Таможенная декларация", DocumentCategory.PURCHASE, True, "Валютный контроль"),
    ("ПА-01", "Агентский договор Заказчик–мастер-агент", DocumentCategory.PAYMENT_AGENT, True, "Юридический отдел"),
    ("ПА-02", "Поручение к агентскому договору", DocumentCategory.PAYMENT_AGENT, True, "Юридический отдел"),
    ("ПА-03", "Поручение мастер-агент–агент", DocumentCategory.PAYMENT_AGENT, True, "Юридический отдел"),
    ("ПА-04", "ПП об оплате (Заказчик, с отметкой исполнения)", DocumentCategory.PAYMENT_AGENT, True, "Бухгалтерия"),
    ("ПА-05", "ПП об оплате (казначейство)", DocumentCategory.PAYMENT_AGENT, True, "Бухгалтерия"),
    ("ПА-06", "СВИФТ от агента", DocumentCategory.PAYMENT_AGENT, True, "Валютный контроль"),
    ("ПА-07", "Акт-отчёт Заказчик–мастер-агент", DocumentCategory.PAYMENT_AGENT, True, "Бухгалтерия"),
    ("ПА-08", "СВО о платеже на агента", DocumentCategory.PAYMENT_AGENT, True, "Валютный контроль"),
    ("ПА-09", "Акт-отчёт агент–мастер-агент", DocumentCategory.PAYMENT_AGENT, True, "Бухгалтерия"),
    ("ПА-10", "СВО от агента (если резидент)", DocumentCategory.PAYMENT_AGENT, False, "Валютный контроль"),
    ("ПБ-00", "ДС к договору (оплата от третьих лиц)", DocumentCategory.PAYMENT_BANK, False, "Юридический отдел"),
    ("ПБ-01", "Платёжное поручение / СВИФТ (прямой платёж через банк)", DocumentCategory.PAYMENT_BANK, True, "Бухгалтерия"),
    ("ПБ-02", "Сведения о валютной операции — СВО (прямой платёж через банк)", DocumentCategory.PAYMENT_BANK, True, "Валютный контроль"),
]

DEMO_USERS = [
    ("Сергей Руководителев", "rukovoditel@example.com", UserRole.RUKOVODITEL),
    ("Пётр Исполнителев", "ispolnitel1@example.com", UserRole.ISPOLNITEL),
    ("Анна Исполнителева", "ispolnitel2@example.com", UserRole.ISPOLNITEL),
    ("Иван Заказчиков", "zakazchik1@example.com", UserRole.ZAKAZCHIK),
    ("Мария Заказчикова", "zakazchik2@example.com", UserRole.ZAKAZCHIK),
]


def seed_all(db: Session) -> None:
    _seed_simple(db, Currency, "code", CURRENCIES, lambda v: {"code": v})
    _seed_simple(db, Division, "name", DIVISIONS, lambda v: {"name": v})
    _seed_simple(db, Agent, "code", AGENTS, lambda v: {"code": v})
    _seed_simple(db, BuyerCompany, "name", BUYER_COMPANIES, lambda v: {"name": v})

    for code, name, category, is_required, recipient_service in DOCUMENT_TYPES:
        existing = db.get(DocumentType, code)
        if existing:
            # дозаполняем службу-получатель у существующих типов (BR-123)
            if existing.recipient_service is None:
                existing.recipient_service = recipient_service
        else:
            db.add(DocumentType(code=code, name=name, category=category,
                                is_required_default=is_required, recipient_service=recipient_service))

    for full_name, email, role in DEMO_USERS:
        exists = db.query(User).filter(User.email == email).first()
        if not exists:
            db.add(User(full_name=full_name, email=email, role=role))

    db.commit()


def _seed_simple(db: Session, model, unique_field: str, values: list[str], to_kwargs) -> None:
    for v in values:
        exists = db.query(model).filter(getattr(model, unique_field) == v).first()
        if not exists:
            db.add(model(**to_kwargs(v)))
    db.flush()
