#!/usr/bin/env python3
"""
Add an account from the command line.

A stopgap until administrators can add people from the app. The account is created
exactly the way bootstrap creates the owner: with a generated password and
``must_change_password`` set, so the password printed here opens one door only — the
screen where its holder chooses their own.

The password is printed once, to stdout, and stored nowhere else. Hand it over by a
channel you would trust with a password, and expect it to be dead after first login.

Usage (inside the container, where the dependencies live)::

    docker exec dev-weft-service python3 -m scripts.add_user alice
    docker exec dev-weft-service python3 -m scripts.add_user alice --display-name "Alice"
    docker exec dev-weft-service python3 -m scripts.add_user bob --role admin

``owner`` is not on offer: there is exactly one, and bootstrap made it.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from sqlalchemy import select

from app import config, db
from app.bootstrap import generate_password
from app.models import Role, User
from app.passwords import hash_password


async def add_user(username: str, display_name: str | None, role: Role) -> str:
    password = generate_password()
    maker = db.get_sessionmaker()
    try:
        async with maker() as session:
            existing = await session.execute(select(User).where(User.username == username))
            if existing.scalar_one_or_none() is not None:
                raise SystemExit(f"An account named {username!r} already exists.")

            session.add(
                User(
                    username=username,
                    display_name=display_name or username,
                    password_hash=hash_password(password),
                    role=role,
                    is_active=True,
                    must_change_password=True,
                )
            )
            await session.commit()
    finally:
        await db.dispose_engine()
    return password


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("username")
    parser.add_argument("--display-name")
    parser.add_argument("--role", choices=[Role.user.value, Role.admin.value],
                        default=Role.user.value)
    args = parser.parse_args()

    username = args.username.strip()
    if not username:
        sys.exit("A username cannot be blank.")

    config.load_config()
    password = asyncio.run(add_user(username, args.display_name, Role(args.role)))

    print(f"username: {username}")
    print(f"password: {password}")
    print("This password must be changed at first login.")


if __name__ == "__main__":
    main()
