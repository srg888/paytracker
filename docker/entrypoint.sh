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
exec uvicorn app.main:app --host 0.0.0.0 --port 8000
