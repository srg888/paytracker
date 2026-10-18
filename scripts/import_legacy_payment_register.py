"""Разовый перенос старого Excel-реестра платежей в PayTracker.

Контекст: до PayTracker реестр платежей вёлся в Excel с колонками
№ SD, Дата заявки, Заказчик, Описание, Получатель, Агент, Субагент,
Сумма, Статус, Дата обновления (~60 строк).

Это НЕ постоянная функция приложения (в отличие от app/services/import_refs.py,
который импортирует справочники через UI раздела «Настройки» и может запускаться
многократно) — это одноразовый скрипт для разового переноса исторических данных.
Поэтому он живёт в scripts/, а не в app/, и не привязан ни к одному HTTP-роуту.

КАК ПОЛЬЗОВАТЬСЯ
-----------------
1. Выгрузите Excel-реестр в CSV (UTF-8, разделитель — запятая или ';', см. --delimiter)
   с колонками ровно как в Excel: № SD, Дата заявки, Заказчик, Описание, Получатель,
   Агент, Субагент, Сумма, Статус, Дата обновления.
   Если не все платежи в рублях — добавьте колонку "Валюта" (ISO-код: USD/EUR/...);
   для строк без этой колонки используется DEFAULT_CURRENCY ниже.

2. Откройте этот файл и заполните три вещи под свои данные (см. "НАСТРОЙТЕ ПЕРЕД
   ЗАПУСКОМ" ниже): DEFAULT_DIVISION_NAME, IMPORTED_BY_EMAIL, STATUS_MAP.

3. Сначала ВСЕГДА запускайте без --commit (сухой прогон, ничего не пишет в БД):

       python -m scripts.import_legacy_payment_register reestr.csv

   Скрипт выведет: сколько строк разобрано, какие Заказчики/Агенты/Субагенты не
   нашлись в справочниках (создайте их — см. [[Администрирование справочников]] —
   или поправьте написание в CSV), какие значения "Статус" не описаны в STATUS_MAP.
   Пока в выводе есть хоть одна ошибка — ничего коммитить нельзя, сухой прогон
   покажет 0 к записи.

4. Когда сухой прогон проходит без ошибок — запустите по-настоящему:

       python -m scripts.import_legacy_payment_register reestr.csv --commit

ЧТО ДЕЛАЕТ СКРИПТ С КАЖДОЙ СТРОКОЙ
-----------------------------------
- Создаёт Request (type=PAYMENT) + PaymentRequest НАПРЯМУЮ (как app/seed.py для
  демо-данных), а не через status_machine — это перенос истории, а не живой
  рабочий процесс, проходить все переходы статусов для этого не нужно.
- Системный номер (`REQ-000123`) генерируется как обычно, по id; старый номер
  "№ SD" не теряется — он дописывается в начало description: "[№ SD: 482] <описание>".
- created_at/updated_at НЕ берутся из текущего времени (хотя это server_default
  в модели) — явно проставляются из "Дата заявки"/"Дата обновления", чтобы история
  не выглядела так, будто все 60 платежей сделаны сегодня одной секундой.
- Статус — из STATUS_MAP (вы должны явно сопоставить текстовые статусы из Excel
  статусам PayTracker — автоматического угадывания нет НАМЕРЕННО, ошибка здесь
  исказила бы отчётность).
- Заказчик — ищется по User.full_name (точное совпадение после .strip()). Если
  нет — импорт этой строки не выполняется, имя попадает в отчёт "не найдено".
  Заводить пользователей-Заказчиков заранее — через обычную админку/БД, этот
  скрипт пользователей не создаёт (сознательно: создание пользователя — не то,
  что должно происходить неявно внутри импорта платежей).
- Агент/Субагент — ищутся по Agent.name (точное совпадение после .strip()),
  без учёта регистра. Если не найден — поле остаётся пустым, импорт строки НЕ
  блокируется (в отличие от Заказчика): агент — вспомогательная информация,
  отсутствие которой не мешает иметь исторический факт платежа в системе.
- Банковские реквизиты получателя (страна/адрес/банк/IBAN/SWIFT), которых в
  Excel-реестре не было, заполняются явным текстом-плейсхолдером (см.
  PLACEHOLDER ниже) — НЕ пустой строкой и не выдумкой, чтобы в интерфейсе было
  сразу видно: "это перенесённая историческая запись, реквизитов для неё нет",
  а не "кто-то забыл их заполнить".
- Одна запись RequestStatusHistory (статус "импортирован сразу в текущем
  статусе") и одна запись AuditLog с action_type=REQUEST_IMPORTED — чтобы в
  аудите каждой такой заявки было явно видно, что она перенесена, а не создана
  через интерфейс (см. BR в Vault после переноса).

ЧЕГО СКРИПТ НЕ ДЕЛАЕТ
----------------------
- Не трогает курсы ЦБ (rate_at_request и т.п. остаются NULL) — это исторические
  факты, пересчитывать их задним числом смысла нет; при необходимости их можно
  актуализировать вручную в карточке заявки после переноса (Руководитель теперь
  может редактировать любую заявку — см. BR-018).
- Не запускается повторно поверх уже перенесённых строк: не ищет дубликаты по
  "№ SD" при повторном запуске — если нужно перезапустить, сначала удалите ранее
  импортированные заявки (Руководитель может удалить несколько сразу, BR-018)
  либо добавьте такую проверку сами перед вторым запуском.
"""
from __future__ import annotations

import argparse
import csv
import sys
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from app.db.session import SessionLocal
from app.models.agent import Agent
from app.models.audit_log import AuditLog
from app.models.currency import Currency
from app.models.division import Division
from app.models.enums import AuditActionType, RequestStatus, RequestType
from app.models.request import PaymentRequest, Request
from app.models.status_history import RequestStatusHistory
from app.models.user import User

# ========================= НАСТРОЙТЕ ПЕРЕД ЗАПУСКОМ =========================

# Подразделение, которому приписываются все перенесённые заявки (в старом
# реестре колонки "Подразделение" не было — одно значение на весь импорт).
DEFAULT_DIVISION_NAME = "Казначейство"

# Валюта для строк без отдельной колонки "Валюта" в CSV.
DEFAULT_CURRENCY_CODE = "RUB"

# Email пользователя, от чьего имени выполняется импорт (created_by_id).
# Должен существовать в БД. Исполнитель заявкам не назначается (executor_id
# остаётся NULL) — в старом реестре не было данных о том, кто именно исполнял.
IMPORTED_BY_EMAIL = "manager@example.com"  # <-- замените на реальный email

# Сопоставление текстовых значений колонки "Статус" в Excel статусам PayTracker.
# Пример — замените ключи на ваши реальные значения (смотрите, что выведет
# сухой прогон в разделе "Статусы без сопоставления", если что-то забыли):
STATUS_MAP: dict[str, RequestStatus] = {
    "Оплачено": RequestStatus.CLOSED,
    "Закрыто": RequestStatus.CLOSED,
    "Отменено": RequestStatus.REJECTED,
    "Отказано": RequestStatus.REJECTED,
    "В архиве": RequestStatus.ARCHIVED,
}

PLACEHOLDER = "— (не зафиксировано при переносе из Excel-реестра)"

# ==============================================================================


def _parse_date(raw: str) -> date:
    raw = raw.strip()
    for fmt in ("%d.%m.%Y", "%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"не удалось разобрать дату: {raw!r} (ожидается ДД.ММ.ГГГГ или ГГГГ-ММ-ДД)")


def _parse_amount(raw: str) -> Decimal:
    raw = raw.strip().replace(" ", "").replace("\xa0", "").replace(",", ".")
    try:
        return Decimal(raw)
    except InvalidOperation as e:
        raise ValueError(f"не удалось разобрать сумму: {raw!r}") from e


def _next_request_number(req_id: int) -> str:
    """Та же схема, что и в app/main.py — REQ-NNNNNN по id."""
    return f"REQ-{req_id:06d}"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("csv_path", help="путь к CSV-выгрузке старого реестра")
    parser.add_argument("--delimiter", default=",", help="разделитель колонок в CSV (по умолчанию ',')")
    parser.add_argument("--commit", action="store_true", help="реально записать в БД (без флага — только проверка)")
    args = parser.parse_args()

    db = SessionLocal()
    errors: list[str] = []
    warnings: list[str] = []
    to_create: list[dict] = []

    division = db.query(Division).filter(Division.name == DEFAULT_DIVISION_NAME).first()
    if not division:
        errors.append(f"Подразделение {DEFAULT_DIVISION_NAME!r} не найдено — создайте его заранее.")

    imported_by = db.query(User).filter(User.email == IMPORTED_BY_EMAIL).first()
    if not imported_by:
        errors.append(f"Пользователь с email {IMPORTED_BY_EMAIL!r} не найден — поправьте IMPORTED_BY_EMAIL в скрипте.")

    default_currency = db.query(Currency).filter(Currency.code == DEFAULT_CURRENCY_CODE).first()
    if not default_currency:
        errors.append(f"Валюта {DEFAULT_CURRENCY_CODE!r} не найдена в справочнике валют.")

    with open(args.csv_path, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f, delimiter=args.delimiter)
        required_cols = {"№ SD", "Дата заявки", "Заказчик", "Описание", "Получатель",
                          "Агент", "Субагент", "Сумма", "Статус", "Дата обновления"}
        missing_cols = required_cols - set(reader.fieldnames or [])
        if missing_cols:
            print(f"В CSV не хватает колонок: {sorted(missing_cols)}", file=sys.stderr)
            sys.exit(1)

        for row_num, row in enumerate(reader, start=2):  # строка 1 — заголовок
            sd_number = row["№ SD"].strip()
            row_errors: list[str] = []

            try:
                request_date = _parse_date(row["Дата заявки"])
            except ValueError as e:
                row_errors.append(str(e))
                request_date = None

            try:
                updated_date = _parse_date(row["Дата обновления"]) if row["Дата обновления"].strip() else request_date
            except ValueError as e:
                row_errors.append(str(e))
                updated_date = request_date

            try:
                amount = _parse_amount(row["Сумма"])
            except ValueError as e:
                row_errors.append(str(e))
                amount = None

            requester_name = row["Заказчик"].strip()
            requester = db.query(User).filter(User.full_name == requester_name).first() if requester_name else None
            if not requester:
                row_errors.append(f"строка {row_num} (№ SD {sd_number}): Заказчик {requester_name!r} не найден в системе")

            status_raw = row["Статус"].strip()
            status = STATUS_MAP.get(status_raw)
            if status is None:
                row_errors.append(f"строка {row_num} (№ SD {sd_number}): статус {status_raw!r} не описан в STATUS_MAP")

            currency_code = (row.get("Валюта") or DEFAULT_CURRENCY_CODE).strip() or DEFAULT_CURRENCY_CODE
            currency = db.query(Currency).filter(Currency.code == currency_code).first()
            if not currency:
                row_errors.append(f"строка {row_num} (№ SD {sd_number}): валюта {currency_code!r} не найдена")

            agent_name = row["Агент"].strip()
            agent = None
            if agent_name:
                agent = db.query(Agent).filter(Agent.name.ilike(agent_name)).first()
                if not agent:
                    warnings.append(f"строка {row_num} (№ SD {sd_number}): агент {agent_name!r} не найден — поле останется пустым")

            subagent_name = row["Субагент"].strip()
            subagent = None
            if subagent_name:
                subagent = db.query(Agent).filter(Agent.name.ilike(subagent_name)).first()
                if not subagent:
                    warnings.append(f"строка {row_num} (№ SD {sd_number}): субагент {subagent_name!r} не найден — поле останется пустым")

            if row_errors:
                errors.extend(row_errors)
                continue

            to_create.append(dict(
                sd_number=sd_number,
                request_date=request_date,
                updated_date=updated_date,
                description=row["Описание"].strip(),
                recipient_name=row["Получатель"].strip() or "—",
                amount=amount,
                currency=currency,
                status=status,
                requester=requester,
                agent=agent,
                subagent=subagent,
            ))

    print(f"Разобрано строк: {len(to_create) + len(errors)}; готово к переносу: {len(to_create)}; ошибок: {len(errors)}")
    if warnings:
        print("\nПРЕДУПРЕЖДЕНИЯ (не блокируют перенос):")
        for w in warnings:
            print("  -", w)
    if errors:
        print("\nОШИБКИ (блокируют перенос — исправьте и запустите сухой прогон снова):")
        for e in errors:
            print("  -", e)
        print("\nНичего не записано." if not args.commit else "\nПрервано ДО записи в БД.")
        db.close()
        sys.exit(1)

    if not args.commit:
        print("\nСухой прогон успешен, ошибок нет. Повторите команду с флагом --commit, чтобы записать в БД.")
        db.close()
        return

    created_numbers: list[str] = []
    for row in to_create:
        req = Request(
            type=RequestType.PAYMENT,
            status=row["status"],
            title=f"[№ SD: {row['sd_number']}] {row['recipient_name']}",
            description=f"[Перенесено из Excel-реестра, № SD: {row['sd_number']}] {row['description']}",
            division_id=division.id,
            created_by_id=imported_by.id,
            requester_id=row["requester"].id,
            executor_id=None,
            # Временный уникальный номер, чтобы не словить конфликт уникальности
            # между flush() соседних строк (все они переименуются ниже после
            # получения id — "TEMP" на всех сразу столкнулся бы с UNIQUE).
            number=f"TEMP-{row['sd_number']}",
            created_at=datetime.combine(row["request_date"], datetime.min.time()),
            updated_at=datetime.combine(row["updated_date"], datetime.min.time()),
            closed_at=datetime.combine(row["updated_date"], datetime.min.time()) if row["status"] == RequestStatus.CLOSED else None,
        )
        db.add(req)
        db.flush()  # получить req.id для номера
        req.number = _next_request_number(req.id)

        pd = PaymentRequest(
            request_id=req.id,
            purpose=row["description"],
            payment_purpose=row["description"],
            amount=row["amount"],
            currency_id=row["currency"].id,
            recipient_name=row["recipient_name"],
            recipient_country=PLACEHOLDER,
            recipient_address=PLACEHOLDER,
            recipient_bank=PLACEHOLDER,
            account_number_iban=PLACEHOLDER,
            swift_bic=PLACEHOLDER,
            agent_id=row["agent"].id if row["agent"] else None,
            subagent_id=row["subagent"].id if row["subagent"] else None,
        )
        db.add(pd)

        db.add(RequestStatusHistory(
            request_id=req.id,
            from_status=None,
            to_status=row["status"],
            changed_by_id=imported_by.id,
            comment="Импортировано из Excel-реестра платежей (scripts/import_legacy_payment_register.py)",
            changed_at=datetime.combine(row["request_date"], datetime.min.time()),
        ))
        db.add(AuditLog(
            entity_type="request",
            entity_id=req.id,
            user_id=imported_by.id,
            action_type=AuditActionType.REQUEST_IMPORTED,
            old_value=f"№ SD: {row['sd_number']}",
        ))
        created_numbers.append(f"{req.number} (№ SD {row['sd_number']})")

    db.commit()
    db.close()
    print(f"\nГотово. Создано заявок: {len(created_numbers)}.")
    for n in created_numbers:
        print("  -", n)


if __name__ == "__main__":
    main()
