import xml.etree.ElementTree as ET
from datetime import date
from decimal import Decimal

import httpx
from sqlalchemy.orm import Session

from app.models.currency import Currency, ExchangeRate

CBR_URL = "https://www.cbr.ru/scripts/XML_daily.asp"


def _fetch_from_cbr() -> dict[str, Decimal]:
    """Возвращает {код_валюты: курс_к_рублю}. Бросает исключение, если cbr.ru недоступен —
    вызывающий код должен ловить и переходить на fallback (см. заявки на платежи.md)."""
    response = httpx.get(CBR_URL, timeout=5.0)
    response.raise_for_status()
    root = ET.fromstring(response.content)
    rates: dict[str, Decimal] = {"RUB": Decimal("1")}
    for valute in root.findall("Valute"):
        code = valute.findtext("CharCode")
        nominal = Decimal(valute.findtext("Nominal").replace(",", "."))
        value = Decimal(valute.findtext("Value").replace(",", "."))
        if code:
            rates[code] = value / nominal
    return rates


def get_rate_for_today(db: Session, currency: Currency) -> tuple[Decimal | None, bool]:
    """Возвращает (курс, is_stale). Логика ровно как описано в заявки на платежи.md:
    1. Пробуем получить свежий курс с cbr.ru и сохранить в кэш.
    2. Если cbr.ru недоступен — берём последний сохранённый курс, помечаем is_stale=True.
    3. Если в кэше вообще ничего нет — возвращаем (None, True)."""
    today = date.today()

    if currency.code == "RUB":
        return Decimal("1"), False

    try:
        rates = _fetch_from_cbr()
        if currency.code in rates:
            value = rates[currency.code]
            existing = (
                db.query(ExchangeRate)
                .filter(ExchangeRate.currency_id == currency.id, ExchangeRate.rate_date == today)
                .first()
            )
            if existing:
                existing.rate_value = value
                existing.is_stale = False
            else:
                db.add(
                    ExchangeRate(
                        currency_id=currency.id, rate_date=today, rate_value=value, is_stale=False
                    )
                )
            db.flush()
            return value, False
    except (httpx.HTTPError, ET.ParseError):
        pass  # cbr.ru недоступен — переходим на fallback ниже

    last_known = (
        db.query(ExchangeRate)
        .filter(ExchangeRate.currency_id == currency.id)
        .order_by(ExchangeRate.rate_date.desc())
        .first()
    )
    if last_known:
        return last_known.rate_value, True

    return None, True
