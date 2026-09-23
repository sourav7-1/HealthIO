"""Create an administrator account (admins cannot self-register).

Usage (from apps/api):
    uv run python -m scripts.create_admin --email admin@yourdomain.in --name "Ops Admin"

The password is read interactively (or from HIO_ADMIN_PASSWORD for automation) and is
never echoed or logged. The account is created ACTIVE with a verified email, and the
action is written to the audit log.
"""

import argparse
import asyncio
import getpass
import os
import sys
from datetime import UTC, datetime

from app.core.client import ClientInfo
from app.core.config import get_settings
from app.core.crypto import email_index, normalize_email
from app.core.db import Database
from app.core.enums import Role
from app.core.ids import uuid7
from app.modules.audit.service import record_client_event
from app.modules.identity import repo
from app.modules.identity.models import User, UserRole, UserStatus
from app.modules.identity.security import Passwords
from app.modules.identity.service import password_problems


async def create_admin(email: str, name: str, password: str) -> int:
    settings = get_settings()
    problems = password_problems(password, email=email, settings=settings)
    if problems:
        print("Password rejected: " + " ".join(problems), file=sys.stderr)
        return 2
    db = Database(settings)
    try:
        async with db.sessionmaker() as session:
            if await repo.get_user_by_email_index(session, email_index(email)):
                print("An account with this email already exists.", file=sys.stderr)
                return 1
            user_id = uuid7()
            session.add(
                User(
                    id=user_id,
                    email=normalize_email(email),
                    email_bidx=email_index(email),
                    display_name=name,
                    password_hash=Passwords(settings).hash(password),
                    password_changed_at=datetime.now(UTC),
                    status=UserStatus.ACTIVE,
                    email_verified_at=datetime.now(UTC),
                    created_by=user_id,
                    updated_by=user_id,
                )
            )
            await session.flush()
            session.add(UserRole(user_id=user_id, role=Role.ADMIN, created_by=user_id))
            await record_client_event(
                session,
                ClientInfo(ip=None, user_agent="scripts.create_admin", request_id=None),
                action="admin.created",
                actor_user_id=None,
                resource_type="user",
                resource_id=user_id,
            )
            await session.commit()
            print(f"Created admin {user_id}")
            return 0
    finally:
        await db.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--email", required=True)
    parser.add_argument("--name", required=True)
    args = parser.parse_args()
    password = os.environ.get("HIO_ADMIN_PASSWORD") or getpass.getpass("Password: ")
    sys.exit(asyncio.run(create_admin(args.email, args.name, password)))


if __name__ == "__main__":
    main()
