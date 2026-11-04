"""БР-131: аналитика по агентам (обороты, вознаграждения) и контроль порогов
валютного контроля — в разрезе КОНКРЕТНОГО ДОГОВОРА агента, не агента в целом.

Пороги считаются "на лету" запросом к уже исполненным/закрытым заявкам, а не
хранимым накопительным полем — на объёме в несколько сотен заявок это дёшево,
а хранимый счётчик рискует разойтись с реальностью при редактировании/удалении
заявок задним числом (Руководитель может и то, и другое — BR-018).
"""
from __future__ import annotations

from decimal import Decimal

from sqlalchemy.orm import Session

from app.models.agent_contract import AgentContract
from app.models.enums import AuditActionType
from app.models.payment_execution import PaymentExecution
from app.models.request import PaymentRequest

THRESHOLD_PAID = Decimal("2800000")       # БР-131, порог 1: уже выплачено
THRESHOLD_PLANNED = Decimal("2900000")    # БР-131, порог 2: выплачено + планируется


def _to_rub(amount: Decimal | None, currency_code: str, rate: Decimal | None) -> Decimal | None:
    """Конвертация суммы в рубли. Возвращает None, если сконвертировать
    нечем (нет курса и валюта не рубль) — такие суммы в текущей версии
    НЕ включаются в накопленный итог по порогам (см. известное ограничение
    в [[Аналитика и контроль по агентам (валютный контроль)]] в vault) —
    лучше заниженная сумма с явным предупреждением, чем тихо неверная."""
    if amount is None:
        return None
    if currency_code == "RUB":
        return amount
    if rate is None:
        return None
    return (amount * rate).quantize(Decimal("0.01"))


def _paid_payment_requests_for_contract(db: Session, contract_id: int) -> list[PaymentRequest]:
    """"Выплачено" = есть хотя бы одна запись фактического исполнения
    (PaymentExecution) — это момент, когда деньги реально ушли агенту, а не
    момент, когда заявка дошла до бюрократически финального статуса «Закрыта»
    (между mark_execution_done и Closed ещё идут проверка документов и
    согласование Руководителя — для валютного контроля это уже не имеет
    значения, деньги уже переведены)."""
    return (
        db.query(PaymentRequest)
        .join(PaymentExecution, PaymentExecution.payment_request_id == PaymentRequest.request_id)
        .filter(PaymentRequest.contract_id == contract_id)
        .distinct()
        .all()
    )


def get_contract_paid_commission_rub(db: Session, contract_id: int) -> tuple[Decimal, bool]:
    """Сумма вознаграждений, фактически выплаченных по договору, в рублях.
    Второй элемент — True, если хотя бы одна заявка не удалось сконвертировать
    (нет курса) и поэтому сумма занижена."""
    total = Decimal("0")
    incomplete = False
    for pr in _paid_payment_requests_for_contract(db, contract_id):
        if pr.agreed_commission_amount is None:
            continue
        rub = _to_rub(pr.agreed_commission_amount, pr.currency.code, pr.rate_at_execution or pr.rate_at_request)
        if rub is None:
            incomplete = True
            continue
        total += rub
    return total, incomplete


def get_contract_turnover(db: Session, contract_id: int) -> dict:
    """А1: оборот платежей через договор, по валютам + итог в рублях.
    Рублёвый итог берётся из PaymentExecution.amount_rub — уже зафиксированной
    суммы на момент подтверждения исполнения (BR-061/062/091), а не
    пересчитывается заново по курсу (это было бы менее точно и дублировало бы
    уже посчитанное значение)."""
    by_currency: dict[str, Decimal] = {}
    total_rub = Decimal("0")
    incomplete = False
    for pr in _paid_payment_requests_for_contract(db, contract_id):
        code = pr.currency.code
        by_currency[code] = by_currency.get(code, Decimal("0")) + pr.amount
        for ex in pr.executions:
            if ex.amount_rub is None:
                incomplete = True
            else:
                total_rub += ex.amount_rub
    return {"by_currency": by_currency, "total_rub": total_rub, "incomplete": incomplete}


def get_contract_commissions(db: Session, contract_id: int) -> dict:
    """А2: вознаграждения по договору — отдельно с НДС / без НДС / не указано."""
    with_vat = Decimal("0")
    without_vat = Decimal("0")
    unspecified = Decimal("0")
    for pr in _paid_payment_requests_for_contract(db, contract_id):
        if pr.agreed_commission_amount is None:
            continue
        rub = _to_rub(pr.agreed_commission_amount, pr.currency.code, pr.rate_at_execution or pr.rate_at_request)
        if rub is None:
            continue
        if pr.commission_includes_vat is True:
            with_vat += rub
        elif pr.commission_includes_vat is False:
            without_vat += rub
        else:
            unspecified += rub
    return {"with_vat_rub": with_vat, "without_vat_rub": without_vat, "unspecified_rub": unspecified}


def _log_threshold_triggered(db: Session, contract: AgentContract, user_id: int, reason: str) -> None:
    from app.models.audit_log import AuditLog  # локальный импорт — избегаем цикла на уровне модуля

    db.add(AuditLog(
        entity_type="agent_contract",
        entity_id=contract.id,
        user_id=user_id,
        action_type=AuditActionType.CURRENCY_CONTROL_THRESHOLD_TRIGGERED,
        old_value=f"contract={contract.number}; agent_id={contract.agent_id}; {reason}",
    ))


def check_threshold_on_execution(db: Session, contract_id: int | None, user_id: int) -> None:
    """БР-131, порог 1 (2 800 000 ₽) — вызывать сразу после записи фактического
    исполнения платежа (mark_done), если у заявки есть договор."""
    if contract_id is None:
        return
    contract = db.get(AgentContract, contract_id)
    if not contract or contract.currency_control_registered:
        return
    total, _incomplete = get_contract_paid_commission_rub(db, contract_id)
    if total >= THRESHOLD_PAID:
        _log_threshold_triggered(db, contract, user_id, f"выплачено {total} ₽ (порог {THRESHOLD_PAID} ₽)")


def check_threshold_on_proposal(
    db: Session, contract_id: int | None, planned_commission_rub: Decimal | None, user_id: int
) -> None:
    """БР-131, порог 2 (2 900 000 ₽) — вызывать при согласовании условий
    исполнения (propose_terms), если у предложения есть договор."""
    if contract_id is None or planned_commission_rub is None:
        return
    contract = db.get(AgentContract, contract_id)
    if not contract or contract.currency_control_registered:
        return
    paid, _incomplete = get_contract_paid_commission_rub(db, contract_id)
    projected = paid + planned_commission_rub
    if projected >= THRESHOLD_PLANNED:
        _log_threshold_triggered(
            db, contract, user_id,
            f"выплачено {paid} ₽ + планируется {planned_commission_rub} ₽ = {projected} ₽ (порог {THRESHOLD_PLANNED} ₽)",
        )


def get_triggered_contracts(db: Session) -> list[dict]:
    """Для навязчивого баннера (base.html): все договоры, где контроль не
    поставлен и накопленная сумма достигла хотя бы порога 1. Простая реализация
    (перебор всех незарегистрированных договоров) — приемлема на объёме в
    десятки/сотни договоров; при росте числа агентов стоит кэшировать."""
    result = []
    contracts = db.query(AgentContract).filter(AgentContract.currency_control_registered.is_(False)).all()
    for contract in contracts:
        total, incomplete = get_contract_paid_commission_rub(db, contract.id)
        if total >= THRESHOLD_PAID:
            result.append({"contract": contract, "paid_rub": total, "incomplete": incomplete})
    return result
