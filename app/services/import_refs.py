from __future__ import annotations

import csv
import io
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import BinaryIO

from sqlalchemy.orm import Session

from app.models.agent import Agent
from app.models.buyer_company import BuyerCompany
from app.models.currency import Currency, ExchangeRate


class ImportError_(Exception):
    """Ошибка валидации импорта (пользовательская)."""


def _parse_bool(value: str) -> bool:
    v = (value or "").strip().lower()
    if v in ("1", "true", "да", "yes", "y", "t"):
        return True
    if v in ("0", "false", "нет", "no", "n", "f"):
        return False
    raise ImportError_(f"Недопустимое логическое значение: {value!r}")


def import_agents(db: Session, file: BinaryIO) -> dict:
    """Импорт справочника агентов из CSV: code,name,is_resident,is_active,is_own_company.

    code — обязателен и уникален (ключ обновления). Остальные поля опциональны.
    Возвращает {"added": int, "updated": int, "skipped": int}.
    """
    text = file.read().decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames or "code" not in reader.fieldnames:
        raise ImportError_("CSV должен содержать колонку 'code' (и, опционально, name/is_resident/is_active/is_own_company).")

    added = updated = skipped = 0
    for row in reader:
        code = (row.get("code") or "").strip()
        if not code:
            skipped += 1
            continue
        agent = db.query(Agent).filter(Agent.code == code).first()
        kwargs = {}
        if row.get("name"):
            kwargs["name"] = row["name"].strip()
        if row.get("is_resident"):
            kwargs["is_resident"] = _parse_bool(row["is_resident"])
        if row.get("is_active"):
            kwargs["is_active"] = _parse_bool(row["is_active"])
        if row.get("is_own_company"):
            kwargs["is_own_company"] = _parse_bool(row["is_own_company"])
        if agent:
            for k, v in kwargs.items():
                setattr(agent, k, v)
            updated += 1
        else:
            db.add(Agent(code=code, **kwargs))
            added += 1
    db.commit()
    return {"added": added, "updated": updated, "skipped": skipped}


def import_buyer_companies(db: Session, file: BinaryIO) -> dict:
    """Импорт компаний-покупателей из CSV: name,is_active."""
    text = file.read().decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames or "name" not in reader.fieldnames:
        raise ImportError_("CSV должен содержать колонку 'name' (и, опционально, is_active).")

    added = updated = skipped = 0
    for row in reader:
        name = (row.get("name") or "").strip()
        if not name:
            skipped += 1
            continue
        company = db.query(BuyerCompany).filter(BuyerCompany.name == name).first()
        if row.get("is_active"):
            is_active = _parse_bool(row["is_active"])
        else:
            is_active = True
        if company:
            company.is_active = is_active
            updated += 1
        else:
            db.add(BuyerCompany(name=name, is_active=is_active))
            added += 1
    db.commit()
    return {"added": added, "updated": updated, "skipped": skipped}


def import_exchange_rates(db: Session, file: BinaryIO) -> dict:
    """Импорт курсов валют из CSV: currency,date,rate (ISO-код, YYYY-MM-DD, число).

    Существующая пара currency+date обновляется; новые — добавляются.
    """
    text = file.read().decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    required = {"currency", "date", "rate"}
    if not reader.fieldnames or not required.issubset(reader.fieldnames):
        raise ImportError_("CSV должен содержать колонки: currency, date, rate.")

    added = updated = skipped = 0
    for row in reader:
        code = (row.get("currency") or "").strip().upper()
        date_s = (row.get("date") or "").strip()
        rate_s = (row.get("rate") or "").strip()
        if not code or not date_s or not rate_s:
            skipped += 1
            continue
        currency = db.query(Currency).filter(Currency.code == code).first()
        if not currency:
            skipped += 1
            continue
        try:
            rate_date = date.fromisoformat(date_s)
            rate_value = Decimal(rate_s.replace(",", "."))
        except (ValueError, InvalidOperation):
            skipped += 1
            continue
        existing = (
            db.query(ExchangeRate)
            .filter(ExchangeRate.currency_id == currency.id, ExchangeRate.rate_date == rate_date)
            .first()
        )
        if existing:
            existing.rate_value = rate_value
            existing.source = "import"
            existing.is_stale = False
            updated += 1
        else:
            db.add(ExchangeRate(
                currency_id=currency.id,
                rate_date=rate_date,
                rate_value=rate_value,
                source="import",
                is_stale=False,
            ))
            added += 1
    db.commit()
    return {"added": added, "updated": updated, "skipped": skipped}


IMPORTERS = {
    "agents": import_agents,
    "buyer_companies": import_buyer_companies,
    "exchange_rates": import_exchange_rates,
}
