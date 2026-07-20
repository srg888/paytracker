# PayTracker — Agent Instructions

## Run

```bash
cp .env.example .env                  # fill secrets
docker compose up --build             # starts PostgreSQL + web on :8000
```

Entrypoint script (`docker/entrypoint.sh`) waits for DB, runs `alembic upgrade head`, seeds reference data, then starts `uvicorn app.main:app`.

## Codebase map

| Path | Purpose |
|---|---|
| `app/main.py` | All routes, upload validation, file handling (~1010 lines) |
| `app/models/` | 15 SQLAlchemy 2.0 models, one file each. **Table-per-type** for payment/purchase/consultation (not single-table inheritance) |
| `app/models/enums.py` | All `SAEnum` objects created once here and reused — prevents Alembic autogenerate noise |
| `app/services/status_machine.py` | State machine: DRAFT→NEW→IN_PROGRESS↔CLARIFICATION→AWAITING_CUSTOMER_CONFIRMATION→DOCUMENT_CHECK→CLOSED, plus REJECTED→ARCHIVED |
| `app/services/roles.py` | `is_acting_rukovoditel()` — checks role OR active delegation |
| `app/services/exchange_rate.py` | CBR.ru with cache + stale fallback |
| `app/services/documents.py` | Required-document checklist with per-request overrides |
| `app/security.py` | Session-based auth (no passwords — just `user_id` in session) |
| `app/seed.py` | Demo data: 9 currencies, 3 divisions, 4 agents, 7 buyer companies, 19 document types, 5 users |

## Key conventions

- **No tests exist** — CI only validates migration upgrade/downgrade and model imports. Run `alembic upgrade head && alembic downgrade base && alembic upgrade head` to verify migrations.
- **No linter/formatter** — no ruff, black, or mypy config. No type hints checked at CI.
- **Auth is simplified** — no password, no login endpoint besides `GET /login/{user_id}`. All roles checked in code.
- **All user-facing text is in Russian** — flash messages, templates, comments, seed data.
- **File upload** — extension whitelist, content-type check (client), then libmagic MIME verifies actual bytes. Filenames are UUIDs on disk.
- **Delegation** — at most one active at a time (BR-030). Revocable, logged to audit.

## Architecture notes

- Jinja2 server-side templates (no SPA/API). Routes return `RedirectResponse` or `TemplateResponse`.
- Alembic `env.py` sets `compare_type=True` and reads `DATABASE_URL` from environment.
- `alembic.ini` `sqlalchemy.url` is overridden by `DATABASE_URL` env var at runtime.
- SQLAlchemy naming convention defined in `app/db/base_class.py` — enables stable autogenerate naming.
- Session middleware uses `SESSION_SECRET` env var (dev fallback: `dev-secret-change-me`).
- Data volumes: `db_data` (PostgreSQL) and `uploads_data` (uploaded files) — survive rebuilds.
