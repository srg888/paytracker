import os
import uuid
from datetime import date, datetime

from fastapi import Depends, FastAPI, File, Form, Request, UploadFile
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from starlette.middleware.sessions import SessionMiddleware

from app.db.session import get_db
from app.models.agent import Agent
from app.models.buyer_company import BuyerCompany
from app.models.currency import Currency
from app.models.division import Division
from app.models.document import RequestDocument
from app.models.document_type import DocumentType
from app.models.enums import PaymentMethod, RequestStatus, RequestType, UserRole
from app.models.request import ConsultationRequest, PaymentRequest, PurchaseRequest, Request as RequestModel
from app.models.comment import RequestComment
from app.models.user import User
from app.security import flash, get_current_user, pop_flash
from app.services import status_machine
from app.services.documents import available_document_types, missing_required_documents
from app.services.exchange_rate import get_rate_for_today
from app.services.roles import is_acting_rukovoditel

APP_DIR = os.path.dirname(__file__)
UPLOAD_DIR = os.getenv("UPLOAD_DIR", "/app/uploads")

# Лимиты из заявки на платежи.md / заявки консультации.md: "лимит на 1 файл - 50 Мб,
# лимит на все файлы - 500 Мб. Максимальное количество файлов - 50"
MAX_FILE_SIZE = 50 * 1024 * 1024
MAX_TOTAL_SIZE = 500 * 1024 * 1024
MAX_FILES = 50

app = FastAPI(title="PayTracker MVP")
app.add_middleware(SessionMiddleware, secret_key=os.getenv("SESSION_SECRET", "dev-secret-change-me"))
app.mount("/static", StaticFiles(directory=os.path.join(APP_DIR, "static")), name="static")
templates = Jinja2Templates(directory=os.path.join(APP_DIR, "templates"))


def render(request: Request, template: str, db: Session, **context):
    current_user = get_current_user(request, db)
    return templates.TemplateResponse(
        request,
        template,
        {
            "current_user": current_user,
            "flash_messages": pop_flash(request),
            **context,
        },
    )


async def _save_generic_attachments(
    db: Session, request_id: int, files: list[UploadFile], uploaded_by_id: int, existing_count: int, existing_total_size: int
) -> list[str]:
    """Сохраняет файлы, приложенные Заказчиком (document_type_code=None — не входят
    в формальный чек-лист закрывающих документов). Возвращает список предупреждений
    о файлах, которые не были сохранены из-за превышения лимитов."""
    warnings: list[str] = []
    count = existing_count
    total_size = existing_total_size

    req_dir = os.path.join(UPLOAD_DIR, str(request_id))
    os.makedirs(req_dir, exist_ok=True)

    for file in files:
        if not file or not file.filename:
            continue
        if count >= MAX_FILES:
            warnings.append(f"{file.filename}: превышен лимит в {MAX_FILES} файлов, не загружен")
            continue
        content = await file.read()
        size = len(content)
        if size > MAX_FILE_SIZE:
            warnings.append(f"{file.filename}: превышен лимит 50 Мб на файл, не загружен")
            continue
        if total_size + size > MAX_TOTAL_SIZE:
            warnings.append(f"{file.filename}: превышен общий лимит 500 Мб на заявку, не загружен")
            continue

        safe_name = f"{uuid.uuid4().hex}_{file.filename}"
        dest_path = os.path.join(req_dir, safe_name)
        with open(dest_path, "wb") as f:
            f.write(content)

        db.add(
            RequestDocument(
                request_id=request_id,
                document_type_code=None,
                file_name=file.filename,
                storage_path=dest_path,
                file_size_bytes=size,
                uploaded_by_id=uploaded_by_id,
            )
        )
        count += 1
        total_size += size

    return warnings


# --- Auth (упрощённая для MVP — без пароля, выбор пользователя) ---


@app.get("/")
def index():
    return RedirectResponse("/requests")


@app.get("/login")
def login_page(request: Request, db: Session = Depends(get_db)):
    users = db.query(User).order_by(User.role, User.full_name).all()
    return render(request, "login.html", db, users=users)


@app.get("/login/{user_id}")
def login_as(request: Request, user_id: int, db: Session = Depends(get_db)):
    user = db.get(User, user_id)
    if not user:
        return RedirectResponse("/login")
    request.session["user_id"] = user.id
    return RedirectResponse("/requests")


@app.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login")


def require_login(request: Request, db: Session):
    user = get_current_user(request, db)
    if not user:
        return None
    return user


# --- Requests list & creation ---


@app.get("/requests")
def requests_list(request: Request, db: Session = Depends(get_db)):
    user = require_login(request, db)
    if not user:
        return RedirectResponse("/login")

    query = db.query(RequestModel)
    if user.role == UserRole.ZAKAZCHIK:
        query = query.filter(RequestModel.created_by_id == user.id)
    elif user.role == UserRole.ISPOLNITEL and not is_acting_rukovoditel(db, user):
        query = query.filter(RequestModel.executor_id == user.id)
    # Руководитель (и активный делегат) видит все заявки — многоюрлицовая изоляция не нужна.
    requests_ = query.order_by(RequestModel.id.desc()).all()
    return render(request, "requests_list.html", db, requests=requests_)


def _reference_data(db: Session) -> dict:
    return dict(
        divisions=db.query(Division).all(),
        currencies=db.query(Currency).all(),
        agents=db.query(Agent).filter(Agent.is_active.is_(True)).all(),
        buyer_companies=db.query(BuyerCompany).filter(BuyerCompany.is_active.is_(True)).all(),
    )


@app.get("/requests/new")
def new_request_form(request: Request, db: Session = Depends(get_db)):
    user = require_login(request, db)
    if not user:
        return RedirectResponse("/login")
    return render(request, "request_new.html", db, editing=False, req=None, **_reference_data(db))


def _next_request_number(db: Session) -> str:
    count = db.query(RequestModel).count()
    return f"REQ-{count + 1:04d}"


@app.post("/requests/new")
async def create_request(
    request: Request,
    db: Session = Depends(get_db),
    type: str = Form(...),
    title: str = Form(...),
    division_id: int = Form(...),
    expected_date: str = Form(""),
    description: str = Form(""),
    # payment fields
    purpose: str = Form(""),
    payment_purpose: str = Form(""),
    amount: str = Form(""),
    currency_id: str = Form(""),
    recipient_name: str = Form(""),
    recipient_country: str = Form(""),
    recipient_address: str = Form(""),
    recipient_bank: str = Form(""),
    account_number_iban: str = Form(""),
    swift_bic: str = Form(""),
    additional_payment_info: str = Form(""),
    payment_method: str = Form("bank"),
    agent_id: str = Form(""),
    # purchase fields
    buyer_company_id: str = Form(""),
    purchase_payment_method: str = Form("bank"),
    markup_notes: str = Form(""),
    delivery_date: str = Form(""),
    # consultation fields
    question_description: str = Form(""),
    files: list[UploadFile] = File(default=[]),
):
    user = require_login(request, db)
    if not user:
        return RedirectResponse("/login")

    exp_date = date.fromisoformat(expected_date) if expected_date else None

    req = RequestModel(
        number="",  # выставим после flush, когда узнаем id
        type=RequestType(type),
        status=RequestStatus.DRAFT,
        title=title,
        description=description or None,
        expected_date=exp_date,
        division_id=division_id,
        created_by_id=user.id,
    )
    db.add(req)
    db.flush()
    req.number = _next_request_number(db)

    if type == RequestType.PAYMENT.value:
        currency = db.get(Currency, int(currency_id))
        rate, is_stale = get_rate_for_today(db, currency)
        amount_dec = amount or "0"
        rate_at_request = rate
        amount_rub = (float(amount_dec) * float(rate)) if rate else None
        req.payment_details = PaymentRequest(
            purpose=purpose,
            payment_purpose=payment_purpose,
            amount=amount_dec,
            currency_id=currency.id,
            recipient_name=recipient_name,
            recipient_country=recipient_country,
            recipient_address=recipient_address,
            recipient_bank=recipient_bank,
            account_number_iban=account_number_iban,
            swift_bic=swift_bic,
            additional_payment_info=additional_payment_info or None,
            payment_method=PaymentMethod(payment_method),
            agent_id=int(agent_id) if agent_id else None,
            rate_at_request=rate_at_request,
            amount_rub_at_request=amount_rub,
        )
        if is_stale and rate is not None:
            flash(request, "Курс ЦБ на сегодня недоступен, использован последний известный курс (устаревший).", "error")
        elif rate is None:
            flash(request, "Курс ЦБ не подтверждён — кэш пуст и cbr.ru недоступен.", "error")
    elif type == RequestType.PURCHASE.value:
        req.purchase_details = PurchaseRequest(
            buyer_company_id=int(buyer_company_id),
            payment_method=PaymentMethod(purchase_payment_method),
            markup_notes=markup_notes or None,
            delivery_date=date.fromisoformat(delivery_date) if delivery_date else None,
        )
    elif type == RequestType.CONSULTATION.value:
        req.consultation_details = ConsultationRequest(question_description=question_description)

    db.flush()  # нужен req.id для сохранения вложений
    warnings = await _save_generic_attachments(db, req.id, files, user.id, existing_count=0, existing_total_size=0)

    db.commit()
    flash(request, f"Заявка {req.number} создана (черновик). Не забудьте подать её.")
    for w in warnings:
        flash(request, w, "error")
    return RedirectResponse(f"/requests/{req.id}", status_code=303)


def _require_editable_draft(db: Session, request: Request, request_id: int, user: User):
    """Общая проверка для редактирования: заявка существует, в статусе Черновик,
    и текущий пользователь — её автор. Возвращает (req, error_redirect_or_None)."""
    req = db.get(RequestModel, request_id)
    if not req:
        return None, RedirectResponse("/requests")
    if req.created_by_id != user.id or req.status != RequestStatus.DRAFT:
        flash(request, "Редактировать можно только собственный черновик.", "error")
        return None, RedirectResponse(f"/requests/{request_id}", status_code=303)
    return req, None


@app.get("/requests/{request_id}/edit")
def edit_request_form(request: Request, request_id: int, db: Session = Depends(get_db)):
    user = require_login(request, db)
    if not user:
        return RedirectResponse("/login")
    req, err = _require_editable_draft(db, request, request_id, user)
    if err:
        return err
    return render(request, "request_new.html", db, editing=True, req=req, **_reference_data(db))


@app.post("/requests/{request_id}/edit")
async def edit_request_submit(
    request: Request,
    request_id: int,
    db: Session = Depends(get_db),
    title: str = Form(...),
    division_id: int = Form(...),
    expected_date: str = Form(""),
    description: str = Form(""),
    purpose: str = Form(""),
    payment_purpose: str = Form(""),
    amount: str = Form(""),
    currency_id: str = Form(""),
    recipient_name: str = Form(""),
    recipient_country: str = Form(""),
    recipient_address: str = Form(""),
    recipient_bank: str = Form(""),
    account_number_iban: str = Form(""),
    swift_bic: str = Form(""),
    additional_payment_info: str = Form(""),
    payment_method: str = Form("bank"),
    agent_id: str = Form(""),
    buyer_company_id: str = Form(""),
    purchase_payment_method: str = Form("bank"),
    markup_notes: str = Form(""),
    delivery_date: str = Form(""),
    question_description: str = Form(""),
    files: list[UploadFile] = File(default=[]),
):
    user = require_login(request, db)
    if not user:
        return RedirectResponse("/login")
    req, err = _require_editable_draft(db, request, request_id, user)
    if err:
        return err

    req.title = title
    req.division_id = division_id
    req.expected_date = date.fromisoformat(expected_date) if expected_date else None
    req.description = description or None

    if req.type == RequestType.PAYMENT:
        pd = req.payment_details
        currency = db.get(Currency, int(currency_id))
        rate, is_stale = get_rate_for_today(db, currency)
        amount_dec = amount or "0"
        pd.purpose = purpose
        pd.payment_purpose = payment_purpose
        pd.amount = amount_dec
        pd.currency_id = currency.id
        pd.recipient_name = recipient_name
        pd.recipient_country = recipient_country
        pd.recipient_address = recipient_address
        pd.recipient_bank = recipient_bank
        pd.account_number_iban = account_number_iban
        pd.swift_bic = swift_bic
        pd.additional_payment_info = additional_payment_info or None
        pd.payment_method = PaymentMethod(payment_method)
        pd.agent_id = int(agent_id) if agent_id else None
        pd.rate_at_request = rate
        pd.amount_rub_at_request = (float(amount_dec) * float(rate)) if rate else None
        if rate is None:
            flash(request, "Курс ЦБ не подтверждён — кэш пуст и cbr.ru недоступен.", "error")
    elif req.type == RequestType.PURCHASE:
        pd = req.purchase_details
        pd.buyer_company_id = int(buyer_company_id)
        pd.payment_method = PaymentMethod(purchase_payment_method)
        pd.markup_notes = markup_notes or None
        pd.delivery_date = date.fromisoformat(delivery_date) if delivery_date else None
    elif req.type == RequestType.CONSULTATION:
        req.consultation_details.question_description = question_description

    existing_docs = db.query(RequestDocument).filter(RequestDocument.request_id == req.id).all()
    existing_count = len(existing_docs)
    existing_total_size = sum(d.file_size_bytes for d in existing_docs)
    warnings = await _save_generic_attachments(
        db, req.id, files, user.id, existing_count=existing_count, existing_total_size=existing_total_size
    )

    db.commit()
    flash(request, "Черновик обновлён.")
    for w in warnings:
        flash(request, w, "error")
    return RedirectResponse(f"/requests/{request_id}", status_code=303)


@app.post("/requests/{request_id}/attachments/{doc_id}/delete")
def delete_attachment(request: Request, request_id: int, doc_id: int, db: Session = Depends(get_db)):
    user = require_login(request, db)
    if not user:
        return RedirectResponse("/login")
    req, err = _require_editable_draft(db, request, request_id, user)
    if err:
        return err
    doc = db.get(RequestDocument, doc_id)
    if doc and doc.request_id == request_id and doc.document_type_code is None:
        os.remove(doc.storage_path) if os.path.exists(doc.storage_path) else None
        db.delete(doc)
        db.commit()
        flash(request, "Файл удалён.")
    return RedirectResponse(f"/requests/{request_id}/edit", status_code=303)


# --- Request detail & actions ---


def _build_detail_context(request: Request, db: Session, req: RequestModel, user: User):
    is_creator = req.created_by_id == user.id
    is_executor = req.executor_id == user.id
    acting_ruk = is_acting_rukovoditel(db, user)

    rejection_reason = None
    if req.status == RequestStatus.REJECTED:
        # последняя запись истории с переходом в REJECTED содержит причину
        for h in reversed(req.status_history):
            if h.to_status == RequestStatus.REJECTED:
                rejection_reason = h.comment
                break

    can_upload_document = is_executor and req.status in (
        RequestStatus.IN_PROGRESS,
        RequestStatus.AWAITING_CUSTOMER_CONFIRMATION,
        RequestStatus.DOCUMENT_CHECK,
    )
    attachments = [d for d in req.documents if d.document_type_code is None]

    return dict(
        req=req,
        can_submit=is_creator and req.status == RequestStatus.DRAFT,
        can_edit=is_creator and req.status == RequestStatus.DRAFT,
        can_assign=acting_ruk and req.status == RequestStatus.NEW,
        can_acknowledge_rejection=is_creator and req.status == RequestStatus.REJECTED,
        rejection_reason=rejection_reason,
        can_request_clarification=is_executor and req.status == RequestStatus.IN_PROGRESS,
        can_answer_clarification=is_creator and req.status == RequestStatus.CLARIFICATION,
        can_mark_done=is_executor and req.status == RequestStatus.IN_PROGRESS,
        can_confirm_execution=is_creator and req.status == RequestStatus.AWAITING_CUSTOMER_CONFIRMATION,
        can_confirm_documents=is_executor and req.status == RequestStatus.DOCUMENT_CHECK,
        can_upload_document=can_upload_document,
        attachments=attachments,
        executors=db.query(User)
        .filter(User.role.in_([UserRole.ISPOLNITEL, UserRole.RUKOVODITEL]))
        .order_by(User.role, User.full_name)
        .all(),
        missing_documents=missing_required_documents(db, req) if can_upload_document else [],
        doc_type_options=available_document_types(db, req) if can_upload_document else [],
    )


@app.get("/requests/{request_id}")
def request_detail(request: Request, request_id: int, db: Session = Depends(get_db)):
    user = require_login(request, db)
    if not user:
        return RedirectResponse("/login")
    req = db.get(RequestModel, request_id)
    if not req:
        return RedirectResponse("/requests")
    ctx = _build_detail_context(request, db, req, user)
    return render(request, "request_detail.html", db, **ctx)


def _do_transition(request: Request, db: Session, request_id: int, fn, *args):
    user = require_login(request, db)
    if not user:
        return RedirectResponse("/login")
    req = db.get(RequestModel, request_id)
    if not req:
        return RedirectResponse("/requests")
    try:
        fn(db, req, user, *args)
        db.commit()
        flash(request, "Готово.")
    except status_machine.TransitionError as e:
        db.rollback()
        flash(request, str(e), "error")
    return RedirectResponse(f"/requests/{request_id}", status_code=303)


@app.post("/requests/{request_id}/submit")
def action_submit(request: Request, request_id: int, db: Session = Depends(get_db)):
    return _do_transition(request, db, request_id, status_machine.submit)


@app.post("/requests/{request_id}/assign")
def action_assign(request: Request, request_id: int, executor_id: int = Form(...), db: Session = Depends(get_db)):
    return _do_transition(request, db, request_id, status_machine.assign_executor, executor_id)


@app.post("/requests/{request_id}/reject")
def action_reject(request: Request, request_id: int, reason: str = Form(...), db: Session = Depends(get_db)):
    return _do_transition(request, db, request_id, status_machine.reject, reason)


@app.post("/requests/{request_id}/acknowledge_rejection")
def action_acknowledge(request: Request, request_id: int, db: Session = Depends(get_db)):
    return _do_transition(request, db, request_id, status_machine.acknowledge_rejection)


@app.post("/requests/{request_id}/request_clarification")
def action_request_clarification(
    request: Request, request_id: int, question: str = Form(...), db: Session = Depends(get_db)
):
    return _do_transition(request, db, request_id, status_machine.request_clarification, question)


@app.post("/requests/{request_id}/answer_clarification")
def action_answer_clarification(
    request: Request, request_id: int, answer: str = Form(...), db: Session = Depends(get_db)
):
    return _do_transition(request, db, request_id, status_machine.answer_clarification, answer)


@app.post("/requests/{request_id}/mark_done")
def action_mark_done(request: Request, request_id: int, db: Session = Depends(get_db)):
    user = require_login(request, db)
    if not user:
        return RedirectResponse("/login")
    req = db.get(RequestModel, request_id)
    if not req:
        return RedirectResponse("/requests")
    try:
        status_machine.mark_execution_done(db, req, user)
        # фиксируем курс ЦБ на дату фактического исполнения (см. заявки на платежи.md)
        if req.payment_details:
            rate, is_stale = get_rate_for_today(db, req.payment_details.currency)
            if rate is not None:
                req.payment_details.rate_at_execution = rate
                req.payment_details.amount_rub_at_execution = float(req.payment_details.amount) * float(rate)
        db.commit()
        flash(request, "Исполнение отмечено как завершённое.")
    except status_machine.TransitionError as e:
        db.rollback()
        flash(request, str(e), "error")
    return RedirectResponse(f"/requests/{request_id}", status_code=303)


@app.post("/requests/{request_id}/confirm_execution")
def action_confirm_execution(request: Request, request_id: int, db: Session = Depends(get_db)):
    return _do_transition(request, db, request_id, status_machine.confirm_execution)


@app.post("/requests/{request_id}/confirm_documents")
def action_confirm_documents(request: Request, request_id: int, db: Session = Depends(get_db)):
    return _do_transition(request, db, request_id, status_machine.confirm_documents_complete)


@app.post("/requests/{request_id}/comments")
def action_add_comment(request: Request, request_id: int, content: str = Form(...), db: Session = Depends(get_db)):
    user = require_login(request, db)
    if not user:
        return RedirectResponse("/login")
    db.add(RequestComment(request_id=request_id, author_id=user.id, content=content))
    db.commit()
    return RedirectResponse(f"/requests/{request_id}", status_code=303)


@app.post("/requests/{request_id}/documents")
async def action_upload_document(
    request: Request,
    request_id: int,
    document_type_code: str = Form(...),
    file: UploadFile = None,
    db: Session = Depends(get_db),
):
    user = require_login(request, db)
    if not user:
        return RedirectResponse("/login")

    req_dir = os.path.join(UPLOAD_DIR, str(request_id))
    os.makedirs(req_dir, exist_ok=True)
    safe_name = f"{uuid.uuid4().hex}_{file.filename}"
    dest_path = os.path.join(req_dir, safe_name)
    content = await file.read()
    with open(dest_path, "wb") as f:
        f.write(content)

    db.add(
        RequestDocument(
            request_id=request_id,
            document_type_code=document_type_code,
            file_name=file.filename,
            storage_path=dest_path,
            file_size_bytes=len(content),
            uploaded_by_id=user.id,
        )
    )
    db.commit()
    flash(request, "Документ загружен.")
    return RedirectResponse(f"/requests/{request_id}", status_code=303)
