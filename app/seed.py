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
    # код, название, категория, обязателен по умолчанию
    ("ЗКП-01", "Договор Заказчик–компания-покупатель", DocumentCategory.PURCHASE, True),
    ("ЗКП-02", "Инвойс Заказчик–компания-покупатель", DocumentCategory.PURCHASE, True),
    ("ЗКП-03", "Спецификация/заказ Заказчик–компания-покупатель", DocumentCategory.PURCHASE, False),
    ("ЗПС-01", "Договор компания-покупатель–Поставщик", DocumentCategory.PURCHASE, True),
    ("ЗПС-02", "Инвойс компания-покупатель–Поставщик", DocumentCategory.PURCHASE, True),
    ("ЗПС-03", "Спецификация/заказ компания-покупатель–Поставщик", DocumentCategory.PURCHASE, False),
    ("ТРН-01", "Транспортный документ (CMR/BL/AWB)", DocumentCategory.PURCHASE, True),
    ("ТАМ-01", "Таможенная декларация", DocumentCategory.PURCHASE, True),
    ("ПА-01", "Агентский договор Заказчик–мастер-агент", DocumentCategory.PAYMENT_AGENT, True),
    ("ПА-02", "Поручение к агентскому договору", DocumentCategory.PAYMENT_AGENT, True),
    ("ПА-03", "Поручение мастер-агент–агент", DocumentCategory.PAYMENT_AGENT, True),
    ("ПА-04", "ПП об оплате (Заказчик, с отметкой исполнения)", DocumentCategory.PAYMENT_AGENT, True),
    ("ПА-05", "ПП об оплате (казначейство)", DocumentCategory.PAYMENT_AGENT, True),
    ("ПА-06", "СВИФТ от агента", DocumentCategory.PAYMENT_AGENT, True),
    ("ПА-07", "Акт-отчёт Заказчик–мастер-агент", DocumentCategory.PAYMENT_AGENT, True),
    ("ПА-08", "СВО о платеже на агента", DocumentCategory.PAYMENT_AGENT, True),
    ("ПА-09", "Акт-отчёт агент–мастер-агент", DocumentCategory.PAYMENT_AGENT, True),
    ("ПА-10", "СВО от агента (если резидент)", DocumentCategory.PAYMENT_AGENT, False),
    ("ПБ-00", "ДС к договору (оплата от третьих лиц)", DocumentCategory.PAYMENT_BANK, False),
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

    for code, name, category, is_required in DOCUMENT_TYPES:
        if not db.get(DocumentType, code):
            db.add(DocumentType(code=code, name=name, category=category, is_required_default=is_required))

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
