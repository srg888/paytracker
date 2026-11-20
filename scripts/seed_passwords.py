"""Разовый посев паролей существующим пользователям.

Контекст: до этого изменения вход был без пароля (выбор пользователя из
списка на /login — см. AGENTS.md). После миграции ac5eacaf1219
(password_hash добавлен как NULL) ни один пользователь не может войти, пока
ему не задан пароль — это намеренно, не баг: открытый доступ без пароля не
должен оставаться доступным ни на секунду дольше необходимого.

ДВА РЕЖИМА

1. Интерактивный (по умолчанию, если есть TTY) — не принимает пароли
   аргументами командной строки (остаются в истории шелла/логах), запрашивает
   по одному через getpass (ввод не отображается на экране):

       python -m scripts.seed_passwords

2. Без TTY (например, `docker compose exec` без флага `-it`, или запуск из
   автоматизации без интерактивного stdin) — пароли передаются ОДНИМ разом
   через переменную окружения SEED_PASSWORDS_JSON, JSON-объект email → пароль:

       SEED_PASSWORDS_JSON='{"manager@example.com": "...", "ispolnitel1@example.com": "..."}' \
           python -m scripts.seed_passwords

   ВАЖНО: переменные окружения процесса видны через `docker inspect`/
   `/proc/<pid>/environ` всем, у кого есть доступ к хосту/Docker-демону — это
   более широкий круг, чем "только тот, кто физически ввёл пароль в терминал"
   у интерактивного режима. Используйте этот режим только когда TTY
   действительно недоступен, не как основной способ.

Для каждого активного пользователя без пароля (в обоих режимах) пароль,
который не был передан/введён, останется NULL — вход для этого пользователя
невозможен, пока кто-то не зайдёт и не сбросит ему пароль позже через
Руководителя (см. /settings/references/users).
"""
from __future__ import annotations

import getpass
import json
import os
import sys

from app.db.session import SessionLocal
from app.models.user import User
from app.services.auth import hash_password


def _run_interactive(db, users) -> int:
    set_count = 0
    for u in users:
        if u.password_hash:
            print(f"— {u.full_name} ({u.email}): пароль уже задан, пропускаю.")
            continue
        print(f"\n{u.full_name} ({u.email}, роль {u.role.value})")
        answer = input("  Задать пароль сейчас? [y/N]: ").strip().lower()
        if answer != "y":
            print("  Пропущено — пароль останется не задан, вход для этого пользователя невозможен.")
            continue
        while True:
            pw1 = getpass.getpass("  Новый пароль (не короче 8 символов): ")
            if len(pw1) < 8:
                print("  Слишком короткий, минимум 8 символов.")
                continue
            pw2 = getpass.getpass("  Повторите пароль: ")
            if pw1 != pw2:
                print("  Пароли не совпадают, попробуйте снова.")
                continue
            break
        u.password_hash = hash_password(pw1)
        set_count += 1
        print("  Пароль установлен.")
    return set_count


def _run_from_env(db, users, raw_json: str) -> int:
    try:
        mapping: dict[str, str] = json.loads(raw_json)
    except json.JSONDecodeError as e:
        print(f"SEED_PASSWORDS_JSON — некорректный JSON: {e}", file=sys.stderr)
        sys.exit(1)

    set_count = 0
    by_email = {u.email: u for u in users}
    for email, pw in mapping.items():
        u = by_email.get(email)
        if not u:
            print(f"— {email}: нет такого активного пользователя, пропускаю.")
            continue
        if u.password_hash:
            print(f"— {u.full_name} ({email}): пароль уже задан, пропускаю.")
            continue
        if len(pw) < 8:
            print(f"— {u.full_name} ({email}): пароль короче 8 символов, пропускаю.")
            continue
        u.password_hash = hash_password(pw)
        set_count += 1
        print(f"— {u.full_name} ({email}): пароль установлен.")

    missing = [u.email for u in users if not u.password_hash and u.email not in mapping]
    if missing:
        print(f"\nБез пароля остались (не было в SEED_PASSWORDS_JSON): {', '.join(missing)}")
    return set_count


def main() -> None:
    db = SessionLocal()
    users = db.query(User).filter(User.is_active.is_(True)).order_by(User.role, User.full_name).all()
    if not users:
        print("Активных пользователей не найдено.")
        return

    raw_json = os.getenv("SEED_PASSWORDS_JSON")
    if raw_json:
        set_count = _run_from_env(db, users, raw_json)
    elif sys.stdin.isatty():
        set_count = _run_interactive(db, users)
    else:
        print(
            "Нет интерактивного терминала (TTY) и не задана переменная SEED_PASSWORDS_JSON.\n"
            "Либо запустите с флагом -it (docker compose exec -it ...), либо задайте\n"
            "SEED_PASSWORDS_JSON — см. докстринг этого файла.",
            file=sys.stderr,
        )
        db.close()
        sys.exit(1)

    if set_count == 0:
        print("\nНичего не изменено.")
        db.close()
        return

    db.commit()
    db.close()
    print(f"\nГотово. Паролей задано: {set_count}.")
    print("Сообщите каждому пользователю его пароль лично (устно/в мессенджере) — email-рассылки в системе нет.")


if __name__ == "__main__":
    try:
        main()
    except (KeyboardInterrupt, EOFError):
        print("\nПрервано, изменения не сохранены для текущего пользователя (уже заданные ранее — сохранены).")
        sys.exit(1)
