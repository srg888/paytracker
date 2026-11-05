"""Администрирование справочников (раздел «Настройки»).

Один декларативный реестр вместо пяти отдельных CRUD-страниц: все справочники —
плоские таблицы из 2–6 простых полей, логика просмотра/правки/удаления для них
идентична, различается только набор полей и способ проверки «используется ли
запись в заявках».

Ключевые правила (см. vault: «05. Справочники/Администрирование справочников»):
- запись, на которую ссылаются заявки/документы/курсы, удалять нельзя;
  для справочников с полем is_active вместо удаления предлагается деактивация;
- удаление через CSV-импорт не поддерживается — только явный UI;
- каждое изменение справочника пишется в аудит (REFERENCE_CREATED/UPDATED/DELETED).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Callable

from sqlalchemy import func, or_
from sqlalchemy.orm import Session, selectinload

from app.models.agent import Agent
from app.models.agent_contract import AgentContract
from app.models.buyer_company import BuyerCompany
from app.models.currency import Currency, ExchangeRate
from app.models.document import RequestDocument, RequestDocumentRequirement
from app.models.document_type import DocumentType
from app.models.division import Division

# Отложенный импорт Request делаем внутри функций проверки: модуль импортируется
# из main.py, а Request тянет за собой почти все модели — так избегаем циклов.


@dataclass
class ReferenceField:
    """Поле справочника в UI и правила его ввода."""

    name: str
    label: str
    type: str = "str"          # "str" | "bool" | "enum" | "int"
    required: bool = False
    editable: bool = True
    max_length: int | None = None
    choices: list[tuple[str, str]] = field(default_factory=list)  # для enum: (значение, подпись)
    hint: str | None = None
    # Поле не хранится в колонке модели справочника, а вычисляется из связанных
    # данных (пример: действующий договор агента из agent_contracts).
    virtual: bool = False
    getter: Callable[[Any], Any] | None = None
    # Как записать значение в связанную сущность. Если задан — поле можно
    # править прямо в справочнике: setter сам решит, обновлять существующую
    # связанную запись или создавать новую.
    setter: Callable[[Session, Any, Any], None] | None = None
    # Сущность, в которую пишет setter — для записи в аудит (entity_type).
    writes_to: str | None = None


@dataclass
class ReferenceSpec:
    """Описание одного справочника."""

    key: str
    label: str
    model: Any
    pk_field: str
    fields: list[ReferenceField]
    # сколько записей ссылается на эту (0 — можно удалять)
    usage_check: Callable[[Session, Any], int] | None = None
    # текст для сообщения «нельзя удалить, используется в …»
    usage_label: str = "записях"
    # можно ли вместо удаления предложить деактивацию
    deactivate_field: str | None = None
    # краткое описание для экрана списка справочников
    description: str = ""
    order_by: str | None = None
    # связи, подгружаемые заранее (selectinload) — иначе виртуальные поля
    # вроде «действующий договор» дают по запросу на каждую строку таблицы
    relations: list[Any] = field(default_factory=list)


def _count_requests_by(model_name: str, **conditions) -> Callable[[Session, Any], int]:
    """Считает записи указанной таблицы, ссылающиеся на объект справочника.
    conditions: имя_колонки -> имя_атрибута объекта (обычно "id")."""

    def _check(db: Session, obj: Any) -> int:
        from app.models.request import PaymentRequest, PurchaseRequest, Request

        models = {"request": Request, "purchase": PurchaseRequest, "payment": PaymentRequest}
        model = models[model_name]
        clauses = [getattr(model, column) == getattr(obj, attr) for column, attr in conditions.items()]
        return db.query(func.count(model.request_id if hasattr(model, "request_id") else model.id)).filter(
            or_(*clauses)
        ).scalar() or 0

    return _check


def _agent_usage(db: Session, obj: Agent) -> int:
    """Агента ссылаются и как основного, и как субагента — и в заявках, и в
    предложениях условий. Считаем уникальные заявки, где он встречается."""
    from app.models.payment_terms_proposal import PaymentTermsProposal
    from app.models.request import PaymentRequest, Request

    ids: set[int] = set()
    rows = (
        db.query(PaymentRequest.request_id)
        .filter(or_(PaymentRequest.agent_id == obj.id, PaymentRequest.subagent_id == obj.id))
        .all()
    )
    ids.update(r[0] for r in rows)
    rows = (
        db.query(PaymentTermsProposal.payment_request_id)
        .filter(
            or_(
                PaymentTermsProposal.proposed_agent_id == obj.id,
                PaymentTermsProposal.proposed_subagent_id == obj.id,
            )
        )
        .all()
    )
    if rows:
        from_p = [r[0] for r in rows]
        more = db.query(PaymentRequest.request_id).filter(PaymentRequest.request_id.in_(from_p)).all()
        ids.update(r[0] for r in more)
    return len(ids)


def _currency_usage(db: Session, obj: Currency) -> int:
    """Валюта используется в заявках, фактических исполнениях и кэше курсов."""
    from app.models.payment_execution import PaymentExecution
    from app.models.request import PaymentRequest

    in_requests = db.query(func.count(PaymentRequest.request_id)).filter(
        PaymentRequest.currency_id == obj.id
    ).scalar() or 0
    in_executions = db.query(func.count(PaymentExecution.id)).filter(
        PaymentExecution.currency_id == obj.id
    ).scalar() or 0
    in_rates = db.query(func.count(ExchangeRate.id)).filter(
        ExchangeRate.currency_id == obj.id
    ).scalar() or 0
    return in_requests + in_executions + in_rates


def _document_type_usage(db: Session, obj: Any) -> int:
    """Тип документа используется загруженными документами и переопределениями
    требований по заявкам."""
    docs = db.query(func.count(RequestDocument.id)).filter(
        RequestDocument.document_type_code == obj.code
    ).scalar() or 0
    reqs = db.query(func.count(RequestDocumentRequirement.id)).filter(
        RequestDocumentRequirement.document_type_code == obj.code
    ).scalar() or 0
    return docs + reqs


def _active_contract(obj: Agent):
    """Действующий договор агента на сегодня — для показа в справочнике.

    Договор — отдельная сущность (БР-131), у агента их может быть несколько.
    Если действующих несколько, берётся последний по дате начала: именно он
    определяет текущие условия работы.
    """
    today = date.today()
    active = [c for c in (obj.contracts or []) if c.is_active_on(today)]
    if not active:
        return None
    return max(active, key=lambda c: c.start_date)


def _agent_contract_number(obj: Agent):
    contract = _active_contract(obj)
    return contract.number if contract else None


def _agent_contract_date(obj: Agent):
    contract = _active_contract(obj)
    return contract.start_date if contract else None


def _agent_currency_control(obj: Agent):
    contract = _active_contract(obj)
    return contract.currency_control_registered if contract else None


def _contract_for_write(db: Session, agent: Agent, start_date: date | None = None):
    """Договор агента, в который пишем значения из справочника.

    Если действующий договор есть — правим его. Если нет — создаём новый:
    правка «№ договора» в пустой строке справочника означает «завести договор»,
    это естественнее, чем запрещать ввод.
    """
    contract = _active_contract(agent)
    if contract is not None:
        return contract
    contract = AgentContract(
        agent_id=agent.id,
        number="—",
        start_date=start_date or date.today(),
        currency_control_registered=False,
    )
    db.add(contract)
    agent.contracts.append(contract)
    return contract


def _set_agent_contract_number(db: Session, agent: Agent, value) -> None:
    text = (value or "").strip()
    contract = _contract_for_write(db, agent)
    if not text:
        return  # пустой номер не затирает уже введённый
    contract.number = text


def _set_agent_contract_date(db: Session, agent: Agent, value) -> None:
    if not value:
        return
    parsed = value if isinstance(value, date) else date.fromisoformat(str(value))
    contract = _contract_for_write(db, agent, start_date=parsed)
    contract.start_date = parsed


def _set_agent_currency_control(db: Session, agent: Agent, value) -> None:
    contract = _contract_for_write(db, agent)
    contract.currency_control_registered = bool(value)


REFERENCE_REGISTRY: dict[str, ReferenceSpec] = {
    "divisions": ReferenceSpec(
        key="divisions",
        label="Подразделения",
        model=Division,
        pk_field="id",
        description="Юрлица заказчика, между ними распределяются заявки",
        order_by="name",
        fields=[
            ReferenceField("name", "Название", "str", required=True, max_length=255),
            ReferenceField("is_active", "Активно", "bool"),
        ],
        usage_check=_count_requests_by("request", division_id="id"),
        usage_label="заявках",
        deactivate_field="is_active",
    ),
    "currencies": ReferenceSpec(
        key="currencies",
        label="Валюты",
        model=Currency,
        pk_field="id",
        description="Коды ISO 4217 для сумм платежей и курсов ЦБ",
        order_by="code",
        fields=[
            ReferenceField("code", "Код ISO 4217", "str", required=True, max_length=3,
                           hint="Три латинские буквы: USD, EUR, CNY"),
            ReferenceField("is_active", "Активна", "bool"),
        ],
        usage_check=_currency_usage,
        usage_label="заявках и курсах",
        deactivate_field="is_active",
    ),
    "buyer_companies": ReferenceSpec(
        key="buyer_companies",
        label="Компании-покупатели",
        model=BuyerCompany,
        pk_field="id",
        description="Компании, через которые проходят закупки",
        order_by="name",
        fields=[
            ReferenceField("name", "Название", "str", required=True, max_length=255),
            ReferenceField("is_active", "Активна", "bool"),
            ReferenceField("recipient_service", "Служба-получатель", "str", max_length=255,
                           hint="Необязательно: внутренняя служба"),
        ],
        usage_check=_count_requests_by("purchase", buyer_company_id="id"),
        usage_label="закупках",
        deactivate_field="is_active",
    ),
    "agents": ReferenceSpec(
        key="agents",
        label="Платёжные агенты",
        model=Agent,
        pk_field="id",
        description="Посредники, через которых проходят платежи",
        order_by="code",
        fields=[
            ReferenceField("code", "Код", "str", required=True, max_length=32),
            ReferenceField("name", "Название", "str", max_length=255),
            ReferenceField("is_resident", "Резидент", "bool"),
            ReferenceField("is_active", "Активен", "bool"),
            ReferenceField("is_own_company", "Наша компания", "bool"),
            # БР-131: договор агента — отдельная таблица (у агента их может быть
            # несколько), поэтому здесь только показ действующего договора.
            # Заводится и правится на карточке агента «// АГЕНТЫ».
            ReferenceField("contract_number", "№ договора", "str", virtual=True, max_length=100,
                           getter=_agent_contract_number, setter=_set_agent_contract_number,
                           writes_to="agent_contract",
                           hint="Правит действующий договор; если договора нет — создаст его"),
            ReferenceField("contract_date", "Дата договора", "date", virtual=True,
                           getter=_agent_contract_date, setter=_set_agent_contract_date,
                           writes_to="agent_contract"),
            ReferenceField("currency_control", "Поставлен на валютный контроль", "bool", virtual=True,
                           getter=_agent_currency_control, setter=_set_agent_currency_control,
                           writes_to="agent_contract"),
        ],
        relations=[Agent.contracts],
        usage_check=_agent_usage,
        usage_label="заявках",
        deactivate_field="is_active",
    ),
    "document_types": ReferenceSpec(
        key="document_types",
        label="Типы документов",
        model=DocumentType,
        pk_field="code",
        description="Коды закрывающих документов (ЗКП-01, ПА-01 и т.п.)",
        order_by="code",
        fields=[
            ReferenceField("code", "Код", "str", required=True, max_length=16,
                           editable=False, hint="Код задаётся при создании и не меняется"),
            ReferenceField("name", "Название", "str", required=True, max_length=255),
            ReferenceField("category", "Категория", "enum", required=True, choices=[
                ("purchase", "Закупка"),
                ("payment_agent", "Платёж через агента"),
                ("payment_bank", "Платёж через банк"),
            ]),
            ReferenceField("is_required_default", "Обязателен по умолчанию", "bool"),
            ReferenceField("recipient_service", "Служба-получатель", "str", max_length=128),
            ReferenceField("is_active", "Активен", "bool"),
        ],
        usage_check=_document_type_usage,
        usage_label="документах и требованиях",
        deactivate_field="is_active",
    ),
}


def get_spec(key: str) -> ReferenceSpec | None:
    return REFERENCE_REGISTRY.get(key)


def get_pk_value(spec: ReferenceSpec, obj: Any) -> str:
    return str(getattr(obj, spec.pk_field))


def list_objects(db: Session, spec: ReferenceSpec) -> list[Any]:
    q = db.query(spec.model)
    if spec.relations:
        # предзагрузка одним дополнительным запросом вместо N+1 на виртуальные поля
        q = q.options(*[selectinload(rel) for rel in spec.relations])
    if spec.order_by:
        q = q.order_by(getattr(spec.model, spec.order_by))
    return q.all()


def find_object(db: Session, spec: ReferenceSpec, pk_value: str) -> Any | None:
    column = getattr(spec.model, spec.pk_field)
    if spec.pk_field == "id":
        try:
            pk_value = int(pk_value)
        except (TypeError, ValueError):
            return None
    return db.query(spec.model).filter(column == pk_value).first()


def field_value(obj: Any, f: ReferenceField) -> Any:
    if f.virtual:
        return f.getter(obj) if f.getter else None
    value = getattr(obj, f.name, None)
    if isinstance(value, bool) or value is None:
        return value
    if f.type == "enum":
        return getattr(value, "value", value)
    return value


def display_value(obj: Any, f: ReferenceField) -> str:
    """Значение поля для показа в таблице."""
    value = field_value(obj, f)
    if f.type == "bool":
        # None означает «нет данных» (например у агента нет действующего договора) —
        # это не то же самое, что явное «нет»
        if value is None:
            return "—"
        return "да" if value else "нет"
    if f.type == "enum":
        for raw, label in f.choices:
            if raw == value:
                return label
        return str(value) if value is not None else "—"
    if value is None or value == "":
        return "—"
    if f.type == "date" and isinstance(value, date):
        return value.strftime("%d.%m.%Y")
    return str(value)


def describe_object(spec: ReferenceSpec, obj: Any) -> str:
    """Короткое имя записи для сообщений («Удалено: АП») и подсказок."""
    for name in (spec.pk_field, "name", "code"):
        for f in spec.fields:
            if f.name == name:
                value = getattr(obj, name, None)
                if value:
                    return str(value)
    return str(get_pk_value(spec, obj))


@dataclass
class ValidationResult:
    ok: bool
    values: dict[str, Any] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)


def split_values(spec: ReferenceSpec, values: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Делит проверенные значения на два набора: колонки самой модели справочника
    и значения виртуальных полей (они пишутся в связанную сущность через setter)."""
    stored: dict[str, Any] = {}
    virtual: dict[str, Any] = {}
    for f in spec.fields:
        if f.name not in values:
            continue
        if f.virtual:
            if f.setter is not None:
                virtual[f.name] = values[f.name]
            continue
        stored[f.name] = values[f.name]
    return stored, virtual


def apply_virtual(db: Session, spec: ReferenceSpec, obj: Any, virtual_values: dict[str, Any]) -> list[tuple[str, str | None, str | None]]:
    """Применяет значения виртуальных полей через их setter'ы.

    Возвращает список изменений (имя поля, было, стало) — для сообщения и аудита.
    """
    changes: list[tuple[str, str | None, str | None]] = []
    for f in spec.fields:
        if f.name not in virtual_values or f.setter is None:
            continue
        before = f.getter(obj) if f.getter else None
        f.setter(db, obj, virtual_values[f.name])
        after = f.getter(obj) if f.getter else None
        comparable_before = getattr(before, "isoformat", lambda: before)()
        comparable_after = getattr(after, "isoformat", lambda: after)()
        if comparable_before != comparable_after:
            changes.append((f.name, str(comparable_before), str(comparable_after)))
    return changes

def validate_form(spec: ReferenceSpec, form: dict[str, str], existing: Any | None) -> ValidationResult:
    """Приводит данные формы к значениям модели с проверкой типов и обязательности."""
    values: dict[str, Any] = {}
    errors: list[str] = []

    for f in spec.fields:
        if f.virtual and f.setter is None:
            continue  # поле только для показа: не хранится в модели и не меняется отсюда
        if f.virtual:
            # редактируемое виртуальное поле: значение проверим и применим отдельно,
            # в колонку модели оно не попадает
            pass
        if existing is not None and (not f.editable or f.name == spec.pk_field):
            continue  # при редактировании неизменяемые поля (например код) не трогаем
        if existing is not None and f.name not in form and f.type != "bool":
            # поле не выведено в форму правки — не считаем его пустым
            # (иначе «обязательное» поле давало бы ложную ошибку валидации)
            continue

        raw = form.get(f.name)
        if f.type == "bool":
            # checkbox: отсутствие в форме = снятая галочка
            values[f.name] = raw is not None
            continue

        if f.type == "date":
            text = (raw or "").strip()
            if not text:
                values[f.name] = None
                continue
            try:
                # принимаем формат из поля <input type="date"> (ГГГГ-ММ-ДД) и ДД.ММ.ГГГГ
                parsed = (
                    date.fromisoformat(text)
                    if "-" in text
                    else date(*reversed([int(p) for p in text.split(".")]))
                )
            except (ValueError, TypeError):
                errors.append(f"«{f.label}»: некорректная дата.")
                continue
            values[f.name] = parsed
            continue

        text = (raw or "").strip()
        if f.type == "enum":
            allowed = {choice for choice, _ in f.choices}
            if not text:
                if f.required:
                    errors.append(f"«{f.label}» обязательно для заполнения.")
                values[f.name] = None
            elif text not in allowed:
                errors.append(f"«{f.label}»: недопустимое значение.")
            else:
                values[f.name] = text
            continue

        if not text:
            if f.required:
                errors.append(f"«{f.label}» обязательно для заполнения.")
            values[f.name] = None
            continue
        if f.max_length and len(text) > f.max_length:
            errors.append(f"«{f.label}»: не длиннее {f.max_length} символов.")
            continue
        values[f.name] = text

    return ValidationResult(ok=not errors, values=values, errors=errors)


def check_unique(db: Session, spec: ReferenceSpec, values: dict[str, Any], existing: Any | None) -> list[str]:
    """Проверка уникальности по колонкам с unique=True."""
    errors: list[str] = []
    table = spec.model.__table__
    for column in table.columns:
        if not column.unique:
            continue
        if column.name not in values or values[column.name] in (None, ""):
            continue
        q = db.query(spec.model).filter(column == values[column.name])
        if existing is not None:
            q = q.filter(getattr(spec.model, spec.pk_field) != getattr(existing, spec.pk_field))
        if q.first() is not None:
            label = column.name
            for f in spec.fields:
                if f.name == column.name:
                    label = f.label
            errors.append(f"«{label}»: такое значение уже есть в справочнике.")
    return errors


def usage_count(db: Session, spec: ReferenceSpec, obj: Any) -> int:
    if spec.usage_check is None:
        return 0
    return spec.usage_check(db, obj)
