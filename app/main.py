import os
import asyncio
import uuid
from contextlib import asynccontextmanager
from datetime import date, datetime, timezone
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel
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
from app.models.enums import AuditActionType, PaymentMethod, PaymentTermsDecision, RequestStatus, RequestType, UserRole
from app.models.payment_terms_proposal import PaymentTermsProposal
from app.models.payment_execution import PaymentExecution
from app.models.request import ConsultationRequest, PaymentRequest, PurchaseRequest, Request as RequestModel
from app.models.comment import RequestComment
from app.models.delegation import Delegation
from app.models.user import User
from app.security import flash, get_current_user, pop_flash
from app.services import status_machine, import_refs
from app.services.import_refs import ImportError_
from app.services.documents import available_document_types, document_category_for_request, missing_required_documents
from app.services.exchange_rate import get_rate_for_today
from app.services.roles import is_acting_rukovoditel
from app.services.reports import (
    generate_report,
    generate_report_list,
    request_counts_report,
    payment_sums_report,
    overdue_requests_report,
    sla_report,
    clarification_report,
    request_history_report,
)


class ReportRequestPayload(BaseModel):
    """Тело POST /api/reports/requests."""

    request_id: int
    format: str = "csv"
    user_id: int | None = None  # игнорируется: авторизация из сессии


class ReportListPayload(BaseModel):
    """Тело POST /api/reports/list — экспорт всего видимого списка заявок."""

    format: str = "csv"
    # Опциональные фильтры (как в списке на странице)
    request_type: str | None = None
    status: str | None = None
    division_id: int | None = None
    user_id: int | None = None  # игнорируется: авторизация из сессии

def _rub_amount(amount: str | Decimal, rate: Decimal | None) -> Decimal | None:
    """Decimal-умножение с округлением до копеек — float здесь недопустим
    (см. аудит ChatGPT: бинарная арифметика float даёт неточности на суммах)."""
    if rate is None:
        return None
    return (Decimal(amount) * Decimal(rate)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _audit_field_change(
    db: Session, req: RequestModel, user: User, field_name: str, old_value, new_value
) -> None:
    """Запись изменения поля заявки в аудит (BR-071: 'изменение заявки').
    Пустые/неизменённые значения не логируем."""
    old_s = "" if old_value is None else str(old_value)
    new_s = "" if new_value is None else str(new_value)
    if old_s == new_s:
        return
    db.add(
        AuditLog(
            entity_type="request",
            entity_id=req.id,
            user_id=user.id,
            action_type=AuditActionType.FIELD_CHANGE,
            field_name=field_name,
            old_value=old_s or None,
            new_value=new_s or None,
        )
    )


def _audit_request_deletion(db: Session, req: RequestModel, user: User) -> None:
    """Запись в аудит ДО удаления заявки (BR-018). У audit_log нет FK на requests
    (намеренно — append-only журнал переживает удаление сущности), поэтому после
    удаления восстановить контекст по entity_id через join уже нельзя: здесь же
    дублируем номер/тип/статус/автора/заказчика текстом, чтобы запись аудита
    осталась самодостаточной и понятной даже после того, как заявки больше нет."""
    summary = (
        f"number={req.number}; type={req.type.value}; status={req.status.value}; "
        f"title={req.title}; "
        f"created_by={req.created_by.full_name if req.created_by else req.created_by_id}; "
        f"requester={req.requester.full_name if req.requester else req.requester_id}"
    )
    db.add(AuditLog(
        entity_type="request",
        entity_id=req.id,
        user_id=user.id,
        action_type=AuditActionType.REQUEST_DELETED,
        old_value=summary,
        new_value=None,
    ))


def _delete_request_files(req: RequestModel) -> None:
    """Удалить физические файлы заявки с диска перед удалением строк БД.
    Каскад ORM (cascade="all, delete-orphan") удалит сами записи RequestDocument,
    но не трогает файловую систему — без этого при массовом удалении заявок
    накапливались бы файлы-сироты в /mnt (или настроенном storage-пути)."""
    for doc in req.documents:
        if doc.storage_path and os.path.exists(doc.storage_path):
            try:
                os.remove(doc.storage_path)
            except OSError:
                pass  # не блокируем удаление заявки из-за проблемы с файлом


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


def _background_rate_updater() -> None:
    """Фоновая дозагрузка курсов ЦБ (см. заявки на платежи.md, 'Edge case: cbr.ru недоступен').
    Раз в час пробуем обновить сегодняшний курс для всех активных валют; при успехе —
    значение и is_stale обновляются в кэше (кэш уже наполнен основным вызовом)."""
    import time

    from app.db.session import SessionLocal
    from app.services.exchange_rate import get_rate_for_today

    while True:
        try:
            db = SessionLocal()
            try:
                currencies = db.query(Currency).all()
                for c in currencies:
                    get_rate_for_today(db, c)  # обновит кэш, если cbr.ru доступен
                db.commit()
            finally:
                db.close()
        except Exception:
            pass  # не роняем воркер из-за единичного сбоя
        time.sleep(3600)


@asynccontextmanager
async def lifespan(_: FastAPI):
    task = asyncio.create_task(asyncio.to_thread(_background_rate_updater))
    yield
    task.cancel()


app = FastAPI(title="PayTracker MVP", lifespan=lifespan)
app.add_middleware(SessionMiddleware, secret_key=_get_session_secret())
app.mount("/static", StaticFiles(directory=os.path.join(APP_DIR, "static")), name="static")
app.mount("/uploads", StaticFiles(directory=UPLOAD_DIR), name="uploads")
templates = Jinja2Templates(directory=os.path.join(APP_DIR, "templates"))


_STATUS_RU = {
    "draft": "Черновик",
    "new": "Новая заявка",
    "in_progress": "В работе",
    "clarification": "Уточнение",
    "rejected": "Отклонена",
    "archived": "Архив",
    "awaiting_customer_confirmation": "Ожидает подтверждения",
    "document_check": "Проверка документов",
    "closed": "Закрыта",
}

templates.env.globals["status_ru"] = _STATUS_RU.get


def _static_version(filename: str) -> str:
    """Версия статики по mtime файла — подставляется в ссылку (?v=...) в base.html.

    Без этого браузер держит style.css в эвристическом кеше (StaticFiles не отдаёт
    Cache-Control), и после обновления стилей показывается старая версия — из-за чего
    невидимые по умолчанию выпадающие панели выглядели как часть страницы.
    """
    try:
        return str(int(os.path.getmtime(os.path.join(APP_DIR, "static", filename))))
    except OSError:
        return "0"


templates.env.globals["static_version"] = _static_version

def render(request: Request, template: str, db: Session, **context):
    current_user = get_current_user(request, db)
    # Доступ к «Отчётам/Аудиту/Настройкам» — Руководитель и Исполнители.
    # (Делегатами могут быть только они же — см. матрицу прав, так что роль покрывает и делегатов.)
    can_view_analytics = bool(
        current_user and current_user.role in (UserRole.RUKOVODITEL, UserRole.ISPOLNITEL)
    )
    can_manage_delegations = bool(current_user and current_user.role == UserRole.RUKOVODITEL)
    return templates.TemplateResponse(
        request,
        template,
        {
            "current_user": current_user,
            "flash_messages": pop_flash(request),
            "can_view_analytics": can_view_analytics,
            "can_manage_delegations": can_manage_delegations,
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
    # BR-002: неактивный пользователь не может входить в систему
    if not user.is_active:
        flash(request, "Учётная запись деактивирована. Обратитесь к администратору.", "error")
        return RedirectResponse("/login")
    request.session["user_id"] = user.id
    db.add(AuditLog(
        entity_type="user",
        entity_id=user.id,
        user_id=user.id,
        action_type=AuditActionType.LOGIN,
    ))
    db.commit()
    return RedirectResponse("/requests")


@app.get("/logout")
def logout(request: Request, db: Session = Depends(get_db)):
    user_id = request.session.get("user_id")
    user = db.get(User, user_id) if user_id else None
    request.session.clear()
    if user:
        db.add(AuditLog(
            entity_type="user",
            entity_id=user.id,
            user_id=user.id,
            action_type=AuditActionType.LOGOUT,
        ))
        db.commit()
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
        query = query.filter(RequestModel.requester_id == user.id)
    requests_ = query.order_by(RequestModel.id.desc()).all()
    return render(request, "requests_list.html", db, requests=requests_)


@app.post("/requests/bulk_delete")
def bulk_delete_requests(request: Request, db: Session = Depends(get_db), ids: list[str] = Form(...)):
    """BR-018: Руководитель может удалить несколько заявок сразу из списка заявок."""
    user = _require_rukovoditel(request, db)
    if not user:
        return RedirectResponse("/requests", status_code=303)

    deleted_numbers: list[str] = []
    for raw_id in ids:
        if not raw_id.isdigit():
            continue
        req = db.get(RequestModel, int(raw_id))
        if not req:
            continue
        _audit_request_deletion(db, req, user)
        _delete_request_files(req)
        deleted_numbers.append(req.number)
        db.delete(req)

    db.commit()
    if deleted_numbers:
        flash(request, f"Удалено заявок: {len(deleted_numbers)} ({', '.join(deleted_numbers)}).")
    else:
        flash(request, "Ничего не удалено — отмеченные заявки не найдены.", "error")
    return RedirectResponse("/requests", status_code=303)


# --- Экспорт отчётов (JSON API для кнопок CSV/XLSX в списке заявок) ---


@app.post("/api/reports/requests")
def api_report_request(payload: ReportRequestPayload, request: Request, db: Session = Depends(get_db)):
    """Сгенерировать отчёт (CSV/XLSX) по одной заявке и вернуть URL для скачивания.

    Фронтенд (requests_list.html) шлёт сюда POST с JSON {request_id, format, user_id}.
    user_id в теле игнорируется — авторизация берётся из сессии.
    """
    user = require_login(request, db)
    if not user:
        return JSONResponse({"error": "Не авторизован"}, status_code=401)

    export_format = (payload.format or "csv").lower()
    if export_format not in {"csv", "xlsx"}:
        return JSONResponse({"error": "Недопустимый формат. Доступно: csv, xlsx"}, status_code=400)

    try:
        report = generate_report(db, payload.request_id, export_format, user.id)
    except HTTPException as exc:
        return JSONResponse({"error": exc.detail}, status_code=exc.status_code)
    except Exception as exc:  # непредвиденная ошибка генерации
        return JSONResponse({"error": f"Не удалось сгенерировать отчёт: {exc}"}, status_code=500)

    filename = Path(report.file_path).name
    return JSONResponse({"download_url": f"/uploads/reports/{filename}"})


@app.post("/api/reports/list")
def api_report_list(payload: ReportListPayload, request: Request, db: Session = Depends(get_db)):
    """Сгенерировать отчёт по всему списку заявок (как на странице /requests).

    Учитывает видимость: ZAKAZCHIK видит только свои заявки.
    Принимает опциональные фильтры для сужения выгрузки.
    """
    user = require_login(request, db)
    if not user:
        return JSONResponse({"error": "Не авторизован"}, status_code=401)

    export_format = (payload.format or "csv").lower()
    if export_format not in {"csv", "xlsx"}:
        return JSONResponse({"error": "Недопустимый формат. Доступно: csv, xlsx"}, status_code=400)

    # Правила видимости как в requests_list()
    filters: dict = {}
    if user.role == UserRole.ZAKAZCHIK:
        filters["requester_id"] = [user.id]
    if payload.request_type:
        filters["type"] = [payload.request_type]
    if payload.status:
        filters["status"] = [payload.status]
    if payload.division_id:
        filters["division_id"] = [payload.division_id]

    # По умолчанию — все осмысленные колонки (включая тип-специфичные)
    selected_fields = [
        "ID", "Номер", "Заголовок", "Описание", "Тип", "Статус", "Ожидаемая дата",
        "Создал", "Инициатор", "Исполнитель", "Подразделение", "Сумма", "Валюта",
        "Отправлено", "Закрыто",
        "Назначение платёжа", "Получатель", "Банк получателя", "IBAN", "SWIFT",
        "Метод оплаты", "Агент",
        "Метод оплаты (закупка)", "Покупатель", "Наценка", "Дата поставки",
        "Консультация",
    ]

    try:
        report = generate_report_list(db, selected_fields, filters, export_format, user.id)
    except HTTPException as exc:
        return JSONResponse({"error": exc.detail}, status_code=exc.status_code)
    except Exception as exc:
        return JSONResponse({"error": f"Не удалось сгенерировать отчёт: {exc}"}, status_code=500)

    filename = Path(report.file_path).name
    return JSONResponse({"download_url": f"/uploads/reports/{filename}"})


def _reference_data(db: Session) -> dict:
    return dict(
        divisions=db.query(Division).all(),
        currencies=db.query(Currency).all(),
        agents=db.query(Agent).filter(Agent.is_active.is_(True)).all(),
        buyer_companies=db.query(BuyerCompany).filter(BuyerCompany.is_active.is_(True)).all(),
        zakazchiki=db.query(User).filter(
            User.role == UserRole.ZAKAZCHIK, User.is_active.is_(True)
        ).order_by(User.full_name).all(),
    )


_VALID_REQUEST_TYPES = {"payment", "purchase", "consultation"}


@app.get("/requests/new")
def new_request_type_select(request: Request, db: Session = Depends(get_db)):
    user = require_login(request, db)
    if not user:
        return RedirectResponse("/login")
    return render(request, "request_type_select.html", db)


@app.get("/requests/new/{req_type}")
def new_request_form(request: Request, req_type: str, db: Session = Depends(get_db)):
    user = require_login(request, db)
    if not user:
        return RedirectResponse("/login")
    if req_type not in _VALID_REQUEST_TYPES:
        return RedirectResponse("/requests/new")
    ctx = _reference_data(db)
    ctx["can_pick_requester"] = (
        user.role == UserRole.RUKOVODITEL or is_acting_rukovoditel(db, user)
    )
    ctx["can_create_as_executor"] = (
        user.role == UserRole.ISPOLNITEL and not is_acting_rukovoditel(db, user)
    )
    ctx["can_choose_payment_method"] = _can_choose_payment_method(user)
    ctx["current_user"] = user
    return render(request, "request_new.html", db, editing=False, req=None, req_type=req_type, **ctx)


def _next_request_number(req_id: int) -> str:
    """Строится из id, а не COUNT(*) — при параллельном создании COUNT(*) может
    дать одинаковый номер двум заявкам одновременно, id уникален всегда."""
    return f"REQ-{req_id:06d}"


@app.post("/requests/new/{req_type}")
async def create_request(
    request: Request,
    req_type: str,
    db: Session = Depends(get_db),
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
    # "от имени Заказчика" (BR-015): кто является заказчиком, если не текущий пользователь
    requester_id: str = Form(""),
    files: list[UploadFile] = File(default=[]),
):
    user = require_login(request, db)
    if not user:
        return RedirectResponse("/login")
    if req_type not in _VALID_REQUEST_TYPES:
        return RedirectResponse("/requests/new")

    # BR-016: способ оплаты и агент при платёжной заявке — поле Руководителя/Исполнителя,
    # Заказчик его не выбирает (ни на форме, ни отправкой произвольного значения напрямую).
    can_choose_payment_method = _can_choose_payment_method(user)

    exp_date = date.fromisoformat(expected_date) if expected_date else None

    # BR-015: Руководитель/делегат может создать заявку от имени Заказчика.
    # BR-017: Исполнитель — тоже может создавать заявки и выбирать Заказчика из списка.
    # requester_id из формы берём только если это разрешено и указан реальный пользователь.
    target_requester_id = user.id
    can_pick_requester = user.role == UserRole.RUKOVODITEL or is_acting_rukovoditel(db, user)
    can_create_as_executor = (
        user.role == UserRole.ISPOLNITEL and not can_pick_requester
    )
    if (can_pick_requester or can_create_as_executor) and requester_id:
        try:
            target_requester_id = int(requester_id)
        except ValueError:
            target_requester_id = user.id
        # валидация: указанный пользователь должен быть активным Заказчиком
        requester_user = db.get(User, target_requester_id)
        if not requester_user or requester_user.role != UserRole.ZAKAZCHIK or not requester_user.is_active:
            flash(request, "Указанный Заказчик не найден или неактивен.", "error")
            return RedirectResponse(f"/requests/new/{req_type}", status_code=303)

    req = RequestModel(
        number="",
        type=RequestType(req_type),
        status=RequestStatus.DRAFT,
        title=title,
        description=description or None,
        expected_date=exp_date,
        division_id=division_id,
        created_by_id=user.id,
        requester_id=target_requester_id,
    )
    db.add(req)
    db.flush()
    req.number = _next_request_number(req.id)

    if target_requester_id != user.id:
        db.add(
            AuditLog(
                entity_type="request",
                entity_id=req.id,
                user_id=user.id,
                action_type=AuditActionType.CREATED_FOR_REQUESTER,
                field_name="requester_id",
                old_value=str(user.id),
                new_value=str(target_requester_id),
            )
        )

    if req_type == RequestType.PAYMENT.value:
        if not currency_id:
            flash(request, "Выберите валюту.", "error")
            return RedirectResponse(f"/requests/new/{req_type}", status_code=303)
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
            payment_method=PaymentMethod(payment_method) if (can_choose_payment_method and payment_method) else None,
            agent_id=int(agent_id) if (can_choose_payment_method and agent_id) else None,
            rate_at_request=rate_at_request,
            amount_rub_at_request=amount_rub,
        )
        if is_stale and rate is not None:
            flash(request, "Курс ЦБ на сегодня недоступен, использован последний известный курс (устаревший).", "error")
        elif rate is None:
            flash(request, "Курс ЦБ не подтверждён — кэш пуст и cbr.ru недоступен.", "error")
    elif req_type == RequestType.PURCHASE.value:
        req.purchase_details = PurchaseRequest(
            buyer_company_id=int(buyer_company_id),
            payment_method=PaymentMethod(purchase_payment_method),
            markup_notes=markup_notes or None,
            delivery_date=date.fromisoformat(delivery_date) if delivery_date else None,
        )
    elif req_type == RequestType.CONSULTATION.value:
        req.consultation_details = ConsultationRequest(question_description=question_description)

    db.flush()  # нужен req.id для сохранения вложений
    warnings = await _save_generic_attachments(db, req.id, files, user.id, existing_count=0, existing_total_size=0)

    db.commit()
    flash(request, f"Заявка {req.number} создана (черновик). Не забудьте подать её.")
    for w in warnings:
        flash(request, w, "error")
    return RedirectResponse(f"/requests/{req.id}", status_code=303)


def _can_choose_payment_method(user: User) -> bool:
    """BR-016: способ оплаты (прямой банк / агент) при платёжной заявке выбирают
    Руководитель и Исполнители — это решение казначейства, а не Заказчика."""
    return user.role in (UserRole.RUKOVODITEL, UserRole.ISPOLNITEL)


def _require_editable_draft(db: Session, request: Request, request_id: int, user: User):
    """Общая проверка для редактирования: заявка существует, и либо (а) текущий
    пользователь — её автор и заявка в статусе Черновик, либо (б) пользователь —
    Руководитель (BR-018: Руководитель может редактировать любую заявку, в любом
    статусе, не только свой черновик — каждое изменённое поле пишется в аудит той
    же механикой `_audit_field_change`, что и обычное редактирование черновика).
    Возвращает (req, error_redirect_or_None)."""
    req = db.get(RequestModel, request_id)
    if not req:
        return None, RedirectResponse("/requests")
    is_own_draft = req.created_by_id == user.id and req.status == RequestStatus.DRAFT
    is_manager_override = user.role == UserRole.RUKOVODITEL
    if not is_own_draft and not is_manager_override:
        flash(request, "Редактировать можно только собственный черновик.", "error")
        return None, RedirectResponse(f"/requests/{request_id}", status_code=303)
    if is_manager_override and not is_own_draft:
        # BR-018: отдельная маркерная запись в аудите — редактирование в обход
        # обычных прав (не свой черновик), чтобы это было видно одной строкой,
        # а не только по совокупности отдельных FIELD_CHANGE-записей ниже.
        db.add(AuditLog(
            entity_type="request", entity_id=req.id, user_id=user.id,
            action_type=AuditActionType.MANAGER_EDITED_REQUEST,
            old_value=f"status={req.status.value}; created_by_id={req.created_by_id}",
        ))
    return req, None


@app.get("/requests/{request_id}/edit")
def edit_request_form(request: Request, request_id: int, db: Session = Depends(get_db)):
    user = require_login(request, db)
    if not user:
        return RedirectResponse("/login")
    req, err = _require_editable_draft(db, request, request_id, user)
    if err:
        return err
    return render(
        request, "request_new.html", db, editing=True, req=req,
        can_choose_payment_method=_can_choose_payment_method(user),
        **_reference_data(db),
    )


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

    # BR-016: см. create_request — то же ограничение действует и при редактировании черновика.
    can_choose_payment_method = _can_choose_payment_method(user)

    # Аудит изменений полей (BR-071): фиксируем только реально изменившиеся.
    _audit_field_change(db, req, user, "title", req.title, title)
    req.title = title
    _audit_field_change(db, req, user, "division_id", req.division_id, division_id)
    req.division_id = division_id
    old_expected = req.expected_date.isoformat() if req.expected_date else None
    new_expected = expected_date or None
    _audit_field_change(db, req, user, "expected_date", old_expected, new_expected)
    req.expected_date = date.fromisoformat(expected_date) if expected_date else None
    _audit_field_change(db, req, user, "description", req.description, description or None)
    req.description = description or None

    if req.type == RequestType.PAYMENT:
        if not currency_id:
            flash(request, "Выберите валюту.", "error")
            return RedirectResponse(f"/requests/{request_id}/edit", status_code=303)
        pd = req.payment_details
        currency = db.get(Currency, int(currency_id))
        rate, is_stale = get_rate_for_today(db, currency)
        amount_dec = amount or "0"
        _audit_field_change(db, req, user, "purpose", pd.purpose, purpose)
        pd.purpose = purpose
        _audit_field_change(db, req, user, "payment_purpose", pd.payment_purpose, payment_purpose)
        pd.payment_purpose = payment_purpose
        _audit_field_change(db, req, user, "amount", pd.amount, amount_dec)
        pd.amount = amount_dec
        _audit_field_change(db, req, user, "currency_id", pd.currency_id, currency.id)
        pd.currency_id = currency.id
        _audit_field_change(db, req, user, "recipient_name", pd.recipient_name, recipient_name)
        pd.recipient_name = recipient_name
        _audit_field_change(db, req, user, "recipient_country", pd.recipient_country, recipient_country)
        pd.recipient_country = recipient_country
        _audit_field_change(db, req, user, "recipient_address", pd.recipient_address, recipient_address)
        pd.recipient_address = recipient_address
        _audit_field_change(db, req, user, "recipient_bank", pd.recipient_bank, recipient_bank)
        pd.recipient_bank = recipient_bank
        _audit_field_change(db, req, user, "account_number_iban", pd.account_number_iban, account_number_iban)
        pd.account_number_iban = account_number_iban
        _audit_field_change(db, req, user, "swift_bic", pd.swift_bic, swift_bic)
        pd.swift_bic = swift_bic
        _audit_field_change(db, req, user, "additional_payment_info", pd.additional_payment_info, additional_payment_info or None)
        pd.additional_payment_info = additional_payment_info or None
        if can_choose_payment_method:
            _audit_field_change(db, req, user, "payment_method", pd.payment_method.value if pd.payment_method else None, payment_method or None)
            pd.payment_method = PaymentMethod(payment_method) if payment_method else None
            _audit_field_change(db, req, user, "agent_id", pd.agent_id, int(agent_id) if agent_id else None)
            pd.agent_id = int(agent_id) if agent_id else None
        # иначе (Заказчик) — поле скрыто в форме; значение, выставленное ранее
        # Руководителем/Исполнителем (или ещё не выставленное), не трогаем.
        pd.rate_at_request = rate
        pd.amount_rub_at_request = _rub_amount(amount_dec, rate)
        if rate is None:
            flash(request, "Курс ЦБ не подтверждён — кэш пуст и cbr.ru недоступен.", "error")
    elif req.type == RequestType.PURCHASE:
        pd = req.purchase_details
        _audit_field_change(db, req, user, "buyer_company_id", pd.buyer_company_id, int(buyer_company_id))
        pd.buyer_company_id = int(buyer_company_id)
        _audit_field_change(db, req, user, "purchase_payment_method", pd.payment_method.value if pd.payment_method else None, purchase_payment_method or None)
        pd.payment_method = PaymentMethod(purchase_payment_method)
        _audit_field_change(db, req, user, "markup_notes", pd.markup_notes, markup_notes or None)
        pd.markup_notes = markup_notes or None
        _audit_field_change(db, req, user, "delivery_date", pd.delivery_date.isoformat() if pd.delivery_date else None, delivery_date or None)
        pd.delivery_date = date.fromisoformat(delivery_date) if delivery_date else None
    elif req.type == RequestType.CONSULTATION:
        _audit_field_change(
            db, req, user, "question_description",
            req.consultation_details.question_description, question_description,
        )
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
    is_requester = req.requester_id == user.id
    is_executor = req.executor_id == user.id
    acting_ruk = is_acting_rukovoditel(db, user)

    rejection_reason = None
    if req.status == RequestStatus.REJECTED:
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
    last_proposal = None
    if req.payment_details and req.payment_details.terms_proposals:
        last_proposal = req.payment_details.terms_proposals[-1]

    return dict(
        req=req,
        can_submit=is_creator and req.status == RequestStatus.DRAFT,
        # BR-018: Руководитель может редактировать любую заявку (не только свой черновик).
        can_edit=(is_creator and req.status == RequestStatus.DRAFT) or user.role == UserRole.RUKOVODITEL,
        manager_override_edit=user.role == UserRole.RUKOVODITEL and not (is_creator and req.status == RequestStatus.DRAFT),
        # BR-018: Руководитель может удалить любую заявку (одну или несколько — см. /requests/bulk_delete).
        can_delete=user.role == UserRole.RUKOVODITEL,
        can_assign=acting_ruk and req.status == RequestStatus.NEW,
        can_reassign=acting_ruk and req.status == RequestStatus.IN_PROGRESS,
        can_self_assign=user.role in (UserRole.ISPOLNITEL, UserRole.RUKOVODITEL) and req.status == RequestStatus.NEW and not is_executor,
        can_acknowledge_rejection=is_creator and req.status == RequestStatus.REJECTED,
        rejection_reason=rejection_reason,
        can_request_clarification=(is_executor or acting_ruk) and req.status in (RequestStatus.IN_PROGRESS, RequestStatus.NEW),
        can_answer_clarification=is_requester and req.status == RequestStatus.CLARIFICATION,
        can_propose_terms=is_executor and req.status == RequestStatus.IN_PROGRESS and req.payment_details is not None,
        can_accept_terms=is_requester and req.status == RequestStatus.TERMS_PROPOSED,
        can_reject_terms=is_requester and req.status == RequestStatus.TERMS_PROPOSED,
        last_terms_proposal=last_proposal,
        can_mark_done=is_executor and req.status == RequestStatus.IN_PROGRESS,
        can_confirm_execution=(is_requester or acting_ruk) and req.status == RequestStatus.AWAITING_CUSTOMER_CONFIRMATION,
        can_send_to_manager=is_executor and req.status == RequestStatus.DOCUMENT_CHECK,
        can_manager_close=acting_ruk and req.status == RequestStatus.MANAGER_REVIEW,
        can_rework=acting_ruk and req.status == RequestStatus.MANAGER_REVIEW,
        can_upload_document=can_upload_document,
        acting_ruk=acting_ruk,
        # Полная история заявки доступна тем же ролям, что и раздел «Аудит».
        can_view_history=user.role in (UserRole.RUKOVODITEL, UserRole.ISPOLNITEL),
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
        agents=db.query(Agent).filter(Agent.is_active.is_(True)).all(),
        currencies=db.query(Currency).all(),
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


@app.get("/requests/{request_id}/history")
def request_history_view(request: Request, request_id: int, db: Session = Depends(get_db)):
    """Полная история одной заявки (выгрузка из аудита) — только Руководитель/делегат."""
    user = _require_analytics_access(request, db)
    if not user:
        return RedirectResponse("/requests")
    data = request_history_report(db, request_id)
    if not data:
        return RedirectResponse("/requests")
    return render(request, "reports/request_history.html", db, **data)


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


@app.post("/requests/{request_id}/delete")
def delete_request(request: Request, request_id: int, db: Session = Depends(get_db)):
    """BR-018: Руководитель может удалить одну заявку (в любом статусе)."""
    user = _require_rukovoditel(request, db)
    if not user:
        return RedirectResponse(f"/requests/{request_id}", status_code=303)
    req = db.get(RequestModel, request_id)
    if not req:
        return RedirectResponse("/requests")
    number = req.number
    _audit_request_deletion(db, req, user)
    _delete_request_files(req)
    db.delete(req)
    db.commit()
    flash(request, f"Заявка {number} удалена.")
    return RedirectResponse("/requests", status_code=303)


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
def action_mark_done(
    request: Request,
    request_id: int,
    db: Session = Depends(get_db),
    executed_at: str = Form(""),
    executed_amount: str = Form(""),
    executed_currency_id: str = Form(""),
    executed_method: str = Form(""),
    operation_id: str = Form(""),
    execution_comment: str = Form(""),
):
    user = require_login(request, db)
    if not user:
        return RedirectResponse("/login")
    req = db.get(RequestModel, request_id)
    if not req:
        return RedirectResponse("/requests")
    try:
        # BR-080/081/082: для платёжных заявок фиксируем фактическое исполнение
        # (отдельная сущность). Для закупки/консультации отдельного исполнения нет.
        if req.payment_details:
            exec_date = date.fromisoformat(executed_at) if executed_at else None
            if exec_date is None:
                flash(request, "Укажите дату фактического исполнения.", "error")
                return RedirectResponse(f"/requests/{request_id}", status_code=303)
            if not executed_amount:
                flash(request, "Укажите фактически исполненную сумму.", "error")
                return RedirectResponse(f"/requests/{request_id}", status_code=303)
            if not executed_currency_id:
                flash(request, "Укажите валюту исполнения.", "error")
                return RedirectResponse(f"/requests/{request_id}", status_code=303)
            execution = PaymentExecution(
                payment_request_id=req.payment_details.request_id,
                executed_at=exec_date,
                amount=Decimal(executed_amount),
                currency_id=int(executed_currency_id),
                method=PaymentMethod(executed_method) if executed_method else None,
                operation_id=operation_id or None,
                comment=execution_comment or None,
                created_by_id=user.id,
            )
            db.add(execution)
            db.flush()
            db.add(
                AuditLog(
                    entity_type="request",
                    entity_id=req.id,
                    user_id=user.id,
                    action_type=AuditActionType.EXECUTION_RECORDED,
                    field_name="execution",
                    new_value=f"{executed_amount} {execution.currency.code if execution.currency else ''}",
                )
            )
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
        # раздел "Подтверждение исполнения Заказчиком") — в запись PaymentExecution,
        # а не только в заявку. Обратно совместимо: старые поля заявки тоже заполняем.
        if req.payment_details:
            rate, is_stale = get_rate_for_today(db, req.payment_details.currency)
            if rate is not None:
                req.payment_details.rate_at_execution = rate
                req.payment_details.amount_rub_at_execution = _rub_amount(req.payment_details.amount, rate)
                if req.payment_details.executions:
                    ex = req.payment_details.executions[-1]
                    ex.rate = rate
                    ex.amount_rub = _rub_amount(ex.amount, rate)
        db.commit()
        flash(request, "Исполнение подтверждено.")
    except status_machine.TransitionError as e:
        db.rollback()
        flash(request, str(e), "error")
    return RedirectResponse(f"/requests/{request_id}", status_code=303)


@app.post("/requests/{request_id}/confirm_documents")
def action_confirm_documents(request: Request, request_id: int, db: Session = Depends(get_db)):
    return _do_transition(request, db, request_id, status_machine.confirm_documents_complete)


@app.post("/requests/{request_id}/self_assign")
def action_self_assign(request: Request, request_id: int, db: Session = Depends(get_db)):
    return _do_transition(request, db, request_id, status_machine.self_assign)


@app.post("/requests/{request_id}/propose_terms")
def action_propose_terms(
    request: Request,
    request_id: int,
    db: Session = Depends(get_db),
    proposed_payment_method: str = Form(...),
    proposed_agent_id: str = Form(""),
    proposed_subagent_id: str = Form(""),
    commission_amount: str = Form(...),
    proposed_rate: str = Form(...),
    proposed_execution_deadline: str = Form(""),
):
    user = require_login(request, db)
    if not user:
        return RedirectResponse("/login")
    req = db.get(RequestModel, request_id)
    if not req:
        return RedirectResponse("/requests")
    if not req.payment_details:
        flash(request, "Согласование условий только для платежей.", "error")
        return RedirectResponse(f"/requests/{request_id}", status_code=303)

    agent_id = int(proposed_agent_id) if proposed_agent_id else None
    subagent_id = int(proposed_subagent_id) if proposed_subagent_id else None

    # BR-120: не более двух уровней, Агент и Субагент не могут совпадать.
    if proposed_payment_method == "agent":
        if agent_id is None:
            flash(request, "Укажите Агента для способа 'через агента'.", "error")
            return RedirectResponse(f"/requests/{request_id}", status_code=303)
        if subagent_id is not None and subagent_id == agent_id:
            flash(request, "Агент и Субагент не могут совпадать (BR-120).", "error")
            return RedirectResponse(f"/requests/{request_id}", status_code=303)
    else:  # bank
        agent_id = None
        subagent_id = None

    deadline = date.fromisoformat(proposed_execution_deadline) if proposed_execution_deadline else None
    if deadline is None:
        flash(request, "Укажите срок исполнения (BR-121).", "error")
        return RedirectResponse(f"/requests/{request_id}", status_code=303)

    try:
        proposal = PaymentTermsProposal(
            payment_request_id=req.payment_details.request_id,
            proposed_payment_method=PaymentMethod(proposed_payment_method),
            proposed_agent_id=agent_id,
            proposed_subagent_id=subagent_id,
            commission_amount=commission_amount,
            proposed_rate=proposed_rate,
            proposed_execution_deadline=deadline,
            proposed_by_id=user.id,
        )
        db.add(proposal)
        db.flush()
        status_machine.propose_terms(db, req, user)
        db.commit()
        flash(request, "Условия предложены.")
    except status_machine.TransitionError as e:
        db.rollback()
        flash(request, str(e), "error")
    return RedirectResponse(f"/requests/{request_id}", status_code=303)


@app.post("/requests/{request_id}/accept_terms")
def action_accept_terms(request: Request, request_id: int, db: Session = Depends(get_db)):
    user = require_login(request, db)
    if not user:
        return RedirectResponse("/login")
    req = db.get(RequestModel, request_id)
    if not req:
        return RedirectResponse("/requests")
    try:
        if req.payment_details and req.payment_details.terms_proposals:
            last = req.payment_details.terms_proposals[-1]
            last.decision = PaymentTermsDecision.ACCEPTED
            last.decided_by_id = user.id
            req.payment_details.payment_method = last.proposed_payment_method
            req.payment_details.agent_id = last.proposed_agent_id
            req.payment_details.subagent_id = last.proposed_subagent_id
            req.payment_details.agreed_commission_amount = last.commission_amount
            req.payment_details.agreed_rate = last.proposed_rate
            req.payment_details.agreed_execution_deadline = last.proposed_execution_deadline
        status_machine.accept_terms(db, req, user)
        db.commit()
        flash(request, "Условия приняты.")
    except status_machine.TransitionError as e:
        db.rollback()
        flash(request, str(e), "error")
    return RedirectResponse(f"/requests/{request_id}", status_code=303)


@app.post("/requests/{request_id}/reject_terms")
def action_reject_terms(
    request: Request, request_id: int, reason: str = Form(...), db: Session = Depends(get_db)
):
    user = require_login(request, db)
    if not user:
        return RedirectResponse("/login")
    req = db.get(RequestModel, request_id)
    if not req:
        return RedirectResponse("/requests")
    try:
        if req.payment_details and req.payment_details.terms_proposals:
            last = req.payment_details.terms_proposals[-1]
            last.decision = PaymentTermsDecision.REJECTED
            last.decision_comment = reason
            last.decided_by_id = user.id
        status_machine.reject_terms(db, req, user, reason)
        db.commit()
        flash(request, "Условия отклонены.")
    except status_machine.TransitionError as e:
        db.rollback()
        flash(request, str(e), "error")
    return RedirectResponse(f"/requests/{request_id}", status_code=303)


@app.post("/requests/{request_id}/manager_close")
def action_manager_close(request: Request, request_id: int, db: Session = Depends(get_db)):
    return _do_transition(request, db, request_id, status_machine.manager_close)


@app.post("/requests/{request_id}/rework")
def action_rework(
    request: Request, request_id: int, reason: str = Form(...), db: Session = Depends(get_db)
):
    return _do_transition(request, db, request_id, status_machine.rework_from_manager, reason)


@app.post("/requests/{request_id}/comments")
async def action_add_comment(
    request: Request,
    request_id: int,
    content: str = Form(...),
    file: UploadFile = File(None),
    db: Session = Depends(get_db),
):
    user = require_login(request, db)
    if not user:
        return RedirectResponse("/login")
    comment = RequestComment(request_id=request_id, author_id=user.id, content=content)
    db.add(comment)
    db.flush()

    if file and file.filename:
        is_valid, validation_error = _validate_uploaded_file(file)
        if not is_valid:
            flash(request, validation_error or "Недопустимый файл.", "error")
            db.rollback()
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
                db.rollback()
                return RedirectResponse(f"/requests/{request_id}", status_code=303)
            raise

        extension = Path(file.filename).suffix.lower()
        try:
            mime_ok = _is_actual_mime_allowed(dest_path, extension)
        except Exception:
            mime_ok = False
        if not mime_ok:
            os.remove(dest_path)
            flash(request, "Фактическое содержимое файла не соответствует его расширению.", "error")
            db.rollback()
            return RedirectResponse(f"/requests/{request_id}", status_code=303)

        db.add(RequestDocument(
            request_id=request_id,
            comment_id=comment.id,
            file_name=file.filename,
            storage_path=dest_path,
            file_size_bytes=file_size,
            uploaded_by_id=user.id,
        ))

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
    """Строгая проверка «только Руководитель» — для управления делегированием
    и прочих действий, которые Исполнителю недоступны."""
    user = require_login(request, db)
    if not user or user.role != UserRole.RUKOVODITEL:
        flash(request, "Доступно только Руководителю.", "error")
        return None
    return user


def _require_analytics_access(request: Request, db: Session) -> User | None:
    """Доступ к разделам «Отчёты», «Аудит» и «Настройки»: Руководитель,
    действующий делегат его полномочий и Исполнитель (Заказчику — закрыто)."""
    user = require_login(request, db)
    if not user or user.role not in (UserRole.RUKOVODITEL, UserRole.ISPOLNITEL):
        flash(request, "Доступно Руководителю и Исполнителям.", "error")
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
    reason: str = Form(""),
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

    delegation = Delegation(
        delegator_id=user.id,
        delegate_id=delegate_id,
        start_date=start,
        end_date=end,
        reason=reason or None,
    )
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
    user = _require_analytics_access(request, db)
    if not user:
        return RedirectResponse("/requests")
    entries = db.query(AuditLog).order_by(AuditLog.id.desc()).limit(200).all()
    return render(request, "audit.html", db, entries=entries)


# --- Отчётность (BR-090): количество заявок за период ---


@app.get("/reports")
def reports_view(
    request: Request,
    db: Session = Depends(get_db),
    date_from: str = "",
    date_to: str = "",
    request_type: str = "",
    status: str = "",
):
    user = _require_analytics_access(request, db)
    if not user:
        return RedirectResponse("/requests")

    frm = date.fromisoformat(date_from) if date_from else None
    to = date.fromisoformat(date_to) if date_to else None
    if frm and to and frm > to:
        flash(request, "Дата начала не может быть позже даты окончания.", "error")
        frm = to = None

    data = request_counts_report(
        db,
        date_from=frm,
        date_to=to,
        request_type=request_type or None,
        status=status or None,
    )
    return render(
        request,
        "reports/report_counts.html",
        db,
        data=data,
        date_from=date_from,
        date_to=date_to,
        request_type=request_type,
        status=status,
        request_types=[t.value for t in RequestType],
        statuses=list(_STATUS_RU.keys()),
        status_ru=_STATUS_RU.get,
        type_ru={"payment": "Платёж", "purchase": "Закупка", "consultation": "Консультация"},
    )


@app.get("/reports/sums")
def reports_sums_view(
    request: Request,
    db: Session = Depends(get_db),
    date_from: str = "",
    date_to: str = "",
    currency_id: str = "",
):
    user = _require_analytics_access(request, db)
    if not user:
        return RedirectResponse("/requests")

    frm = date.fromisoformat(date_from) if date_from else None
    to = date.fromisoformat(date_to) if date_to else None
    if frm and to and frm > to:
        flash(request, "Дата начала не может быть позже даты окончания.", "error")
        frm = to = None

    data = payment_sums_report(
        db,
        date_from=frm,
        date_to=to,
        currency_id=int(currency_id) if currency_id else None,
    )
    return render(
        request,
        "reports/report_sums.html",
        db,
        data=data,
        date_from=date_from,
        date_to=date_to,
        currency_id=currency_id,
        currencies=db.query(Currency).order_by(Currency.code).all(),
    )


@app.get("/reports/overdue")
def reports_overdue_view(
    request: Request,
    db: Session = Depends(get_db),
    request_type: str = "",
):
    user = _require_analytics_access(request, db)
    if not user:
        return RedirectResponse("/requests")

    overdue = overdue_requests_report(db, request_type=request_type or None)
    return render(
        request,
        "reports/report_overdue.html",
        db,
        overdue=overdue,
        today=date.today(),
        request_type=request_type,
        request_types=[t.value for t in RequestType],
        type_ru={"payment": "Платёж", "purchase": "Закупка", "consultation": "Консультация"},
    )


@app.get("/reports/sla")
def reports_sla_view(
    request: Request,
    db: Session = Depends(get_db),
    date_from: str = "",
    date_to: str = "",
):
    user = _require_analytics_access(request, db)
    if not user:
        return RedirectResponse("/requests")

    frm = date.fromisoformat(date_from) if date_from else None
    to = date.fromisoformat(date_to) if date_to else None
    if frm and to and frm > to:
        flash(request, "Дата начала не может быть позже даты окончания.", "error")
        frm = to = None

    data = sla_report(db, date_from=frm, date_to=to)
    return render(
        request,
        "reports/report_sla.html",
        db,
        data=data,
        date_from=date_from,
        date_to=date_to,
        type_ru={"payment": "Платёж", "purchase": "Закупка", "consultation": "Консультация"},
    )


@app.get("/reports/clarification")
def reports_clarification_view(
    request: Request,
    db: Session = Depends(get_db),
    date_from: str = "",
    date_to: str = "",
):
    user = _require_analytics_access(request, db)
    if not user:
        return RedirectResponse("/requests")

    frm = date.fromisoformat(date_from) if date_from else None
    to = date.fromisoformat(date_to) if date_to else None
    if frm and to and frm > to:
        flash(request, "Дата начала не может быть позже даты окончания.", "error")
        frm = to = None

    data = clarification_report(db, date_from=frm, date_to=to)
    return render(
        request,
        "reports/report_clarification.html",
        db,
        data=data,
        date_from=date_from,
        date_to=date_to,
    )


@app.get("/settings")
def settings_view(request: Request, db: Session = Depends(get_db)):
    """Раздел «Настройки» — администрирование (справочники и т.п.)."""
    user = _require_analytics_access(request, db)
    if not user:
        return RedirectResponse("/requests")
    return render(request, "settings/settings.html", db)


@app.get("/settings/import")
def settings_import_view(request: Request, db: Session = Depends(get_db)):
    user = _require_analytics_access(request, db)
    if not user:
        return RedirectResponse("/requests")
    return render(request, "settings/import.html", db)


@app.post("/settings/import")
async def settings_import_submit(
    request: Request,
    db: Session = Depends(get_db),
    reference: str = Form(...),
    file: UploadFile = File(None),
):
    user = _require_analytics_access(request, db)
    if not user:
        return RedirectResponse("/requests")

    if reference not in import_refs.IMPORTERS:
        flash(request, "Неизвестный справочник.", "error")
        return RedirectResponse("/settings/import", status_code=303)
    if not file or not file.filename:
        flash(request, "Выберите файл CSV.", "error")
        return RedirectResponse("/settings/import", status_code=303)
    if not file.filename.lower().endswith(".csv"):
        flash(request, "Поддерживается только CSV.", "error")
        return RedirectResponse("/settings/import", status_code=303)

    content = await file.read()
    if not content:
        flash(request, "Файл пуст.", "error")
        return RedirectResponse("/settings/import", status_code=303)

    try:
        from io import BytesIO

        result = import_refs.IMPORTERS[reference](db, BytesIO(content))
    except ImportError_ as e:
        flash(request, str(e), "error")
        return RedirectResponse("/settings/import", status_code=303)

    # Аудит импорта справочника
    db.add(
        AuditLog(
            entity_type="reference",
            entity_id=0,
            user_id=user.id,
            action_type=AuditActionType.REFERENCE_IMPORTED,
            field_name=reference,
            new_value=f"добавлено {result['added']}, обновлено {result['updated']}, пропущено {result['skipped']}",
        )
    )
    db.commit()

    flash(request, f"Импорт «{reference}»: добавлено {result['added']}, обновлено {result['updated']}, пропущено {result['skipped']}.")
    return RedirectResponse("/settings/import", status_code=303)
