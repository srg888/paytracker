#!/bin/sh
set -e

echo "Waiting for database..."
python3 - << 'PYEOF'
import time
import sys
from sqlalchemy import create_engine
from sqlalchemy.exc import OperationalError
from app.db.session import DATABASE_URL

for i in range(30):
    try:
        create_engine(DATABASE_URL).connect().close()
        print("Database is ready")
        sys.exit(0)
    except OperationalError:
        time.sleep(1)
print("Database not reachable after 30s", file=sys.stderr)
sys.exit(1)
PYEOF

echo "Running migrations..."
alembic upgrade head

echo "Seeding reference data..."
python3 - << 'PYEOF'
from app.db.session import SessionLocal
from app.seed import seed_all

db = SessionLocal()
seed_all(db)
db.close()
PYEOF

echo "Starting server..."
# --proxy-headers: доверять X-Forwarded-For от реверс-прокси (nginx/Xray перед
# приложением) — без этого в аудите (LOGIN/LOGOUT/LOGIN_FAILED) писался бы IP
# прокси, а не реального клиента, что бесполезно при разборе инцидентов.
# --forwarded-allow-ips ОБЯЗАТЕЛЕН вместе с --proxy-headers: без него uvicorn
# поверит X-Forwarded-For от любого, кто подключится напрямую — то есть любой
# сможет подделать себе в аудите любой IP. Значение по умолчанию (127.0.0.1)
# верно только если прокси и контейнер на одном хосте и порт web не торчит
# наружу напрямую (см. docker-compose.yml — порт теперь bindится на
# 127.0.0.1, не 0.0.0.0). Если у вас прокси в отдельном контейнере/сети —
# задайте FORWARDED_ALLOW_IPS в .env адресом, с которого реально приходят
# запросы (например, IP docker-бриджа) — ПРОВЕРЬТЕ ЭТО ПРИ ДЕПЛОЕ, значение
# по умолчанию не гарантированно верно для вашей топологии сети.
exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips="${FORWARDED_ALLOW_IPS:-127.0.0.1}"
