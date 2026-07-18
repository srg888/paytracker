import os
import uuid
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, Request, UploadFile
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from starlette.middleware.sessions import SessionMiddleware

from app.db.session import get_db
from app.models.agent import Agent
from app.models.audit_log import AuditLog
from app.models.buyer_company import BuyerCompany
from app.models.currency import Currency
from app.models.division import Division
from app.models.document import RequestDocument, RequestDocumentRequirement
from app.models.document_type import DocumentType
from app.models.enums import AuditActionType, PaymentMethod, RequestStatus, RequestType, UserRole
from app.models.request import ConsultationRequest, PaymentRequest, PurchaseRequest, Request as RequestModel
from app.models.comment import RequestComment
from app.models.delegation import Delegation
from app.models.user import User
from app.security import flash, get_current_user, pop_flash
from app.services import status_machine
from app.services.documents import available_document_types, document_category_for_request, missing_required_documents
from app.services.exchange_rate import get_rate_for_today
from app.services.roles import is_acting_rukovoditel

def _rub_amount(amount: str | Decimal, rate: Decimal | None) -> Decimal | None:
    """Decimal-умножение с округлением до копеек — float здесь недопустим
    (см. аудит ChatGPT: бинарная арифметика float даёт неточности на суммах)."""
    if rate is None:
        return None
    return (Decimal(amount) * Decimal(rate)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


APP_DIR = os.path.dirname(__file__)
UPLOAD_DIR = os.getenv("UPLOAD_DIR", "/app/uploads")

# Лимиты из заявки на платежи.md / заявки консультации.md: "лимит на 1 файл - 50 Мб,
# лимит на все файлы - 500 Мб. Максимальное количество файлов - 50"
MAX_FILE_SIZE = 50 * 1024 * 1024
MAX_TOTAL_SIZE = 500 * 1024 * 1024
MAX_FILES = 50
UPLOAD_CHUNK_SIZE = 1024 * 1024  # 1 MB

ALLOWED_FILE_EXTENSIONS = {
    ".pdf",
    ".doc",
    ".docx",
    ".xls",
    ".xlsx",
    ".csv",
    ".jpg",
    ".jpeg",
    ".png",
}

ALLOWED_CONTENT_TYPES = {
    "application/pdf",
    "application/msword",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.ms-excel",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "text/csv",
    "image/jpeg",
    "image/png",
}

# Реальная сигнатура файла (через libmagic), сверяемая с заявленным расширением.
# Клиентский Content-Type ничего не гарантирует — здесь проверяются байты файла.
# Для .doc/.xls (старые бинарные форматы Office, OLE Compound File) сигнатура
# определяется как application/x-ole-storage — именно её и требуем, а не
# application/octet-stream, чтобы проверка не превращалась в пропуск всего подряд.
_EXTENSION_TO_ACTUAL_MIMES = {
    ".pdf": {"application/pdf"},
    ".doc": {"application/x-ole-storage", "application/x-cfb"},
    ".docx": {
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/zip",
    },
    ".xls": {"application/x-ole-storage", "application/x-cfb"},
    ".xlsx": {
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/zip",
    },
    ".csv": {"text/csv", "text/plain", "application/csv"},
    ".jpg": {"image/jpeg"},
    ".jpeg": {"image/jpeg"},
    ".png": {"image/png"},
}


def _validate_uploaded_file(file: UploadFile) -> tuple[bool, str | None]:
    """Проверяет расширение и заявленный (клиентский) MIME-тип файла.

    Это только первый, дешёвый фильтр. Настоящая проверка содержимого —
    в _is_actual_mime_allowed, которая сверяет реальную сигнатуру файла.
    """
    if not file.filename:
        return False, "Файл не выбран."

    extension = Path(file.filename).suffix.lower()
    if extension not in ALLOWED_FILE_EXTENSIONS:
        return False, "Недопустимый тип файла. Разрешены: " + ", ".join(sorted(ALLOWED_FILE_EXTENSIONS))

    if file.content_type and file.content_type not in ALLOWED_CONTENT_TYPES:
        return False, "Недопустимый MIME-тип файла."

    return True, None


def _stored_filename(original_filename: str) -> str:
    """Имя файла на диске полностью генерируется сервером — оригинальное
    имя пользователя туда никогда не попадает (защита от спецсимволов и
    path traversal). Оригинальное имя сохраняется отдельно, в БД."""
    extension = Path(original_filename).suffix.lower()
    return f"{uuid.uuid4().hex}{extension}"


def _is_actual_mime_allowed(file_path: str, extension: str) -> bool:
    """Сверяет реальную сигнатуру файла (libmagic) с заявленным расширением."""
    import magic

    actual_mime = magic.from_file(file_path, mime=True)
    return actual_mime in _EXTENSION_TO_ACTUAL_MIMES.get(extension, set())


async def _save_upload_with_limit(file: UploadFile, dest_path: str, max_size: int) -> int:
    """Стримит файл на диск чанками, не читая его целиком в память.
    Если размер превышает max_size — обрывает запись и удаляет частичный файл.
    Возвращает фактический сохранённый размер."""
    total_size = 0
    try:
        with open(dest_path, "wb") as output:
            while True:
                chunk = await file.read(UPLOAD_CHUNK_SIZE)
                if not chunk:
                    break
                total_size += len(chunk)
                if total_size > max_size:
                    raise ValueError("FILE_TOO_LARGE")
                output.write(chunk)
    except Exception:
        try:
            os.remove(dest_path)
        except FileNotFoundError:
            pass
        raise
    return total_size


def _get_session_secret() -> str:
    secret = os.getenv("SESSION_SECRET")
    if secret:
        return secret
    if os.getenv("ENVIRONMENT", "development") == "development":
        return "dev-secret-change-me"
    raise RuntimeError(
        "SESSION_SECRET не задан. В production обязательна случайная строка "
        "(например, `openssl rand -hex 32`), небезопасный дефолт использовать нельзя."
    )


app = FastAPI(title="PayTracker MVP")
app.add_middleware(SessionMiddleware, secret_key=_get_session_secret())
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
    if user.role == UserRole.ISPOLNITEL and not is_acting_rukovoditel(db, user):
        flash(request, "Исполнитель не может создавать заявки.", "error")
        return RedirectResponse("/requests")
    return render(request, "request_new.html", db, editing=False, req=None, **_reference_data(db))


def _next_request_number(req_id: int) -> str:
    """Строится из id, а не COUNT(*) — при параллельном создании COUNT(*) может
    дать одинаковый номер двум заявкам одновременно, id уникален всегда."""
    return f"REQ-{req_id:06d}"


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
    if user.role == UserRole.ISPOLNITEL and not is_acting_rukovoditel(db, user):
        flash(request, "Исполнитель не может создавать заявки.", "error")
        return RedirectResponse("/requests")

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
    req.number = _next_request_number(req.id)

    if type == RequestType.PAYMENT.value:
        currency = db.get(Currency, int(currency_id))
        rate, is_stale = get_rate_for_today(db, currency)
        amount_dec = amount or "0"
        rate_at_request = rate
        amount_rub = _rub_amount(amount_dec, rate)
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
        pd.amount_rub_at_request = _rub_amount(amount_dec, rate)
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


@app.post("/requests/{request_id}/documents/{doc_id}/delete")
def delete_document(request: Request, request_id: int, doc_id: int, db: Session = Depends(get_db)):
    user = require_login(request, db)
    if not user:
        return RedirectResponse("/login")
    req = db.get(RequestModel, request_id)
    if not req:
        return RedirectResponse("/requests")
    doc = db.get(RequestDocument, doc_id)
    if not doc or doc.request_id != request_id:
        flash(request, "Документ не найден.", "error")
        return RedirectResponse(f"/requests/{request_id}", status_code=303)

    via_rukovoditel = is_acting_rukovoditel(db, user)
    # Кто может удалять:
    # - автор черновика может удалять вспомогательные файлы (document_type_code=None)
    # - Исполнитель (или Руководитель) может удалять документы, которые сам загрузил
    can_delete = False
    if req.status == RequestStatus.DRAFT and req.created_by_id == user.id and doc.document_type_code is None:
        can_delete = True
    elif doc.uploaded_by_id == user.id and doc.document_type_code is not None and (
        req.executor_id == user.id or via_rukovoditel
    ):
        can_delete = True

    if not can_delete:
        flash(request, "Нет права на удаление этого документа.", "error")
        return RedirectResponse(f"/requests/{request_id}", status_code=303)

    os.remove(doc.storage_path) if os.path.exists(doc.storage_path) else None
    db.delete(doc)
    db.commit()
    flash(request, "Документ удалён.")
    # Перенаправляем обратно туда, откуда пришли: /edit для черновика, /requests/{id} для остальных
    if req.status == RequestStatus.DRAFT:
        return RedirectResponse(f"/requests/{request_id}/edit", status_code=303)
    return RedirectResponse(f"/requests/{request_id}", status_code=303)


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
        can_reassign=acting_ruk and req.status == RequestStatus.IN_PROGRESS,
        can_acknowledge_rejection=is_creator and req.status == RequestStatus.REJECTED,
        rejection_reason=rejection_reason,
        can_request_clarification=is_executor and req.status == RequestStatus.IN_PROGRESS,
        can_answer_clarification=is_creator and req.status == RequestStatus.CLARIFICATION,
        can_mark_done=is_executor and req.status == RequestStatus.IN_PROGRESS,
        can_confirm_execution=(is_creator or acting_ruk) and req.status == RequestStatus.AWAITING_CUSTOMER_CONFIRMATION,
        can_confirm_documents=is_executor and req.status == RequestStatus.DOCUMENT_CHECK,
        can_upload_document=can_upload_document,
        acting_ruk=acting_ruk,
        attachments=attachments,
        executors=db.query(User)
        .filter(User.role.in_([UserRole.ISPOLNITEL, UserRole.RUKOVODITEL]))
        .order_by(User.role, User.full_name)
        .all(),
        missing_documents=missing_required_documents(db, req) if can_upload_document else [],
        doc_type_options=available_document_types(db, req) if can_upload_document else [],
        can_override_requirements=acting_ruk and req.status in (
            RequestStatus.IN_PROGRESS,
            RequestStatus.AWAITING_CUSTOMER_CONFIRMATION,
            RequestStatus.DOCUMENT_CHECK,
        ) and req.type != RequestType.CONSULTATION,
        doc_type_overrides={
            o.document_type_code: o.is_required_override
            for o in db.query(RequestDocumentRequirement).filter(
                RequestDocumentRequirement.request_id == req.id
            )
        },
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
        db.commit()
        flash(request, "Исполнение отмечено как завершённое.")
    except status_machine.TransitionError as e:
        db.rollback()
        flash(request, str(e), "error")
    return RedirectResponse(f"/requests/{request_id}", status_code=303)


@app.post("/requests/{request_id}/confirm_execution")
def action_confirm_execution(request: Request, request_id: int, db: Session = Depends(get_db)):
    user = require_login(request, db)
    if not user:
        return RedirectResponse("/login")
    req = db.get(RequestModel, request_id)
    if not req:
        return RedirectResponse("/requests")
    try:
        status_machine.confirm_execution(db, req, user)
        # фиксируем курс ЦБ на дату фактического исполнения (см. заявки на платежи.md,
        # раздел "Подтверждение исполнения Заказчиком")
        if req.payment_details:
            rate, is_stale = get_rate_for_today(db, req.payment_details.currency)
            if rate is not None:
                req.payment_details.rate_at_execution = rate
                req.payment_details.amount_rub_at_execution = _rub_amount(req.payment_details.amount, rate)
        db.commit()
        flash(request, "Исполнение подтверждено.")
    except status_machine.TransitionError as e:
        db.rollback()
        flash(request, str(e), "error")
    return RedirectResponse(f"/requests/{request_id}", status_code=303)


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

    # Блокируем строку заявки на время проверки лимитов и создания документа —
    # без этого два параллельных upload могут оба пройти проверку общего лимита
    # до того, как любой из них закоммитится, и вместе превысить 500 Мб.
    req = db.query(RequestModel).filter(RequestModel.id == request_id).with_for_update().first()
    if not req:
        return RedirectResponse("/requests")
    if req.executor_id != user.id:
        flash(request, "Загружать документы может только назначенный Исполнитель.", "error")
        return RedirectResponse(f"/requests/{request_id}", status_code=303)
    if req.status not in (RequestStatus.IN_PROGRESS, RequestStatus.AWAITING_CUSTOMER_CONFIRMATION, RequestStatus.DOCUMENT_CHECK):
        flash(request, "Загрузка документов недоступна в текущем статусе заявки.", "error")
        return RedirectResponse(f"/requests/{request_id}", status_code=303)

    doc_type = db.get(DocumentType, document_type_code)
    if not doc_type:
        flash(request, "Неизвестный тип документа.", "error")
        return RedirectResponse(f"/requests/{request_id}", status_code=303)
    if doc_type.category != document_category_for_request(req):
        flash(request, "Этот тип документа не относится к данной заявке.", "error")
        return RedirectResponse(f"/requests/{request_id}", status_code=303)

    is_valid, validation_error = _validate_uploaded_file(file)
    if not is_valid:
        flash(request, validation_error or "Недопустимый файл.", "error")
        return RedirectResponse(f"/requests/{request_id}", status_code=303)

    if len(req.documents) >= MAX_FILES:
        flash(request, f"{file.filename}: превышен лимит {MAX_FILES} файлов, не загружен.", "error")
        return RedirectResponse(f"/requests/{request_id}", status_code=303)

    existing_total = sum(d.file_size_bytes for d in req.documents)
    if existing_total >= MAX_TOTAL_SIZE:
        flash(request, f"{file.filename}: превышен общий лимит 500 Мб на заявку, не загружен.", "error")
        return RedirectResponse(f"/requests/{request_id}", status_code=303)

    req_dir = os.path.join(UPLOAD_DIR, str(request_id))
    os.makedirs(req_dir, exist_ok=True)
    safe_name = _stored_filename(file.filename)
    dest_path = os.path.join(req_dir, safe_name)

    try:
        file_size = await _save_upload_with_limit(file, dest_path, MAX_FILE_SIZE)
    except ValueError as exc:
        if str(exc) == "FILE_TOO_LARGE":
            flash(request, f"{file.filename}: превышен лимит 50 Мб на файл, не загружен.", "error")
            return RedirectResponse(f"/requests/{request_id}", status_code=303)
        raise

    if existing_total + file_size > MAX_TOTAL_SIZE:
        os.remove(dest_path)
        flash(request, f"{file.filename}: превышен общий лимит 500 Мб на заявку, не загружен.", "error")
        return RedirectResponse(f"/requests/{request_id}", status_code=303)

    extension = Path(file.filename).suffix.lower()
    try:
        mime_ok = _is_actual_mime_allowed(dest_path, extension)
    except Exception:
        mime_ok = False
    if not mime_ok:
        os.remove(dest_path)
        flash(request, "Фактическое содержимое файла не соответствует его расширению.", "error")
        return RedirectResponse(f"/requests/{request_id}", status_code=303)

    # Запись документа: file_size_bytes всегда берётся из фактически
    # сохранённого на диск размера (file_size), а не из заявленного клиентом.
    db.add(
        RequestDocument(
            request_id=request_id,
            document_type_code=document_type_code,
            file_name=file.filename,
            storage_path=dest_path,
            file_size_bytes=file_size,
            uploaded_by_id=user.id,
        )
    )
    db.commit()
    flash(request, "Документ загружен.")
    return RedirectResponse(f"/requests/{request_id}", status_code=303)


# --- Delegation management ---


def _require_rukovoditel(request: Request, db: Session) -> User | None:
    user = require_login(request, db)
    if not user or user.role != UserRole.RUKOVODITEL:
        flash(request, "Доступно только Руководителю.", "error")
        return None
    return user


@app.get("/delegations")
def delegations_list(request: Request, db: Session = Depends(get_db)):
    user = _require_rukovoditel(request, db)
    if not user:
        return RedirectResponse("/requests")
    delegations = db.query(Delegation).order_by(Delegation.id.desc()).all()
    executors = db.query(User).filter(
        User.role.in_([UserRole.ISPOLNITEL, UserRole.RUKOVODITEL]),
        User.is_active.is_(True),
    ).order_by(User.full_name).all()
    return render(request, "delegations.html", db, delegations=delegations, executors=executors, current_date=date.today())


@app.post("/delegations/new")
def delegation_create(
    request: Request,
    delegate_id: int = Form(...),
    start_date: str = Form(...),
    end_date: str = Form(...),
    db: Session = Depends(get_db),
):
    user = _require_rukovoditel(request, db)
    if not user:
        return RedirectResponse("/delegations")

    # BR-030: одновременно может быть только одно активное делегирование
    today = date.today()
    active = (
        db.query(Delegation)
        .filter(
            Delegation.start_date <= today,
            Delegation.end_date >= today,
            Delegation.revoked_at.is_(None),
        )
        .first()
    )
    if active:
        flash(request, "Уже есть активное делегирование. Отзовите его перед созданием нового.", "error")
        return RedirectResponse("/delegations", status_code=303)

    delegate = db.get(User, delegate_id)
    if not delegate or not delegate.is_active:
        flash(request, "Исполнитель не найден или неактивен.", "error")
        return RedirectResponse("/delegations", status_code=303)

    start = date.fromisoformat(start_date)
    end = date.fromisoformat(end_date)
    if start > end:
        flash(request, "Дата начала не может быть позже даты окончания.", "error")
        return RedirectResponse("/delegations", status_code=303)

    delegation = Delegation(delegator_id=user.id, delegate_id=delegate_id, start_date=start, end_date=end)
    db.add(delegation)
    db.flush()
    db.add(AuditLog(entity_type="delegation", entity_id=delegation.id, user_id=user.id, action_type=AuditActionType.DELEGATION_STARTED))
    db.commit()
    flash(request, f"Делегирование создано для {delegate.full_name}.")
    return RedirectResponse("/delegations", status_code=303)


@app.post("/delegations/{delegation_id}/revoke")
def delegation_revoke(request: Request, delegation_id: int, db: Session = Depends(get_db)):
    user = _require_rukovoditel(request, db)
    if not user:
        return RedirectResponse("/delegations")

    delegation = db.get(Delegation, delegation_id)
    if not delegation:
        flash(request, "Делегирование не найдено.", "error")
        return RedirectResponse("/delegations", status_code=303)
    if delegation.delegator_id != user.id:
        flash(request, "Отозвать можно только собственное делегирование.", "error")
        return RedirectResponse("/delegations", status_code=303)
    if delegation.revoked_at is not None:
        flash(request, "Делегирование уже отозвано.", "error")
        return RedirectResponse("/delegations", status_code=303)

    delegation.revoked_at = datetime.now(timezone.utc)
    db.add(AuditLog(entity_type="delegation", entity_id=delegation.id, user_id=user.id, action_type=AuditActionType.DELEGATION_REVOKED))
    db.commit()
    flash(request, "Делегирование отозвано.")
    return RedirectResponse("/delegations", status_code=303)


# --- Document requirement overrides ---


@app.post("/requests/{request_id}/document_requirements/{doc_type_code}/toggle")
def toggle_document_requirement(request: Request, request_id: int, doc_type_code: str, db: Session = Depends(get_db)):
    user = _require_rukovoditel(request, db)
    if not user:
        return RedirectResponse(f"/requests/{request_id}", status_code=303)

    req = db.get(RequestModel, request_id)
    if not req:
        flash(request, "Заявка не найдена.", "error")
        return RedirectResponse("/requests", status_code=303)
    if req.status not in (RequestStatus.IN_PROGRESS, RequestStatus.AWAITING_CUSTOMER_CONFIRMATION, RequestStatus.DOCUMENT_CHECK):
        flash(request, "Изменять требования можно только для заявки в работе.", "error")
        return RedirectResponse(f"/requests/{request_id}", status_code=303)

    doc_type = db.get(DocumentType, doc_type_code)
    if not doc_type:
        flash(request, "Тип документа не найден.", "error")
        return RedirectResponse(f"/requests/{request_id}", status_code=303)

    existing = db.query(RequestDocumentRequirement).filter(
        RequestDocumentRequirement.request_id == request_id,
        RequestDocumentRequirement.document_type_code == doc_type_code,
    ).first()

    if existing:
        current_required = existing.is_required_override
        if current_required:
            db.delete(existing)
            new_status = "необязателен"
        else:
            existing.is_required_override = True
            new_status = "обязателен"
    else:
        db.add(RequestDocumentRequirement(
            request_id=request_id,
            document_type_code=doc_type_code,
            is_required_override=not doc_type.is_required_default,
        ))
        new_status = "необязателен" if doc_type.is_required_default else "обязателен"

    db.commit()
    flash(request, f"Требование для {doc_type_code} изменено: {new_status}.")
    return RedirectResponse(f"/requests/{request_id}", status_code=303)


# --- Audit log ---


@app.get("/audit")
def audit_log_view(request: Request, db: Session = Depends(get_db)):
    user = _require_rukovoditel(request, db)
    if not user:
        return RedirectResponse("/requests")
    entries = db.query(AuditLog).order_by(AuditLog.id.desc()).limit(200).all()
    return render(request, "audit.html", db, entries=entries)
