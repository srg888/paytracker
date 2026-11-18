"""Разовый посев паролей существующим пользователям.

Контекст: до этого изменения вход был без пароля (выбор пользователя из
списка на /login — см. AGENTS.md). После миграции ac5eacaf1219
(password_hash добавлен как NULL) ни один пользователь не может войти, пока
ему не задан пароль — это намеренно, не баг: открытый доступ без пароля не
должен оставаться доступным ни на секунду дольше необходимого.

Интерактивный, не принимает пароли аргументами командной строки (они иначе
остаются в истории шелла/логах) — запрашивает по одному через getpass
(ввод не отображается на экране).

ЗАПУСК
    python -m scripts.seed_passwords

Для каждого активного пользователя без пароля спросит, задать ли ему пароль
сейчас (можно пропустить — тогда у него останется NULL, и он не сможет войти,
пока кто-то не зайдёт и не сбросит ему пароль позже через Руководителя,
см. /settings/references/users).
"""
from __future__ import annotations

import getpass
import sys

from app.db.session import SessionLocal
from app.models.user import User
from app.services.auth import hash_password


def main() -> None:
    db = SessionLocal()
    users = db.query(User).filter(User.is_active.is_(True)).order_by(User.role, User.full_name).all()
    if not users:
        print("Активных пользователей не найдено.")
        return

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
