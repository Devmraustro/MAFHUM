"""Grant or revoke the admin role from the operator's command line.

Privilege changes are deliberately **not** an HTTP endpoint. A self-service
"make me admin" route is the most common way an admin panel becomes a
privilege-escalation bug. Instead an operator with database access runs this
tool; every change writes its own audit row.

Usage
-----
    DATABASE_URL=postgresql+psycopg://... python tools/set_admin.py \
        --email operator@example.com --role admin

    python tools/set_admin.py --list

No secret is read from the command line or written to a log. The database URL
comes from the environment (``MAFHUM_DATABASE_URL`` or ``DATABASE_URL``).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from mafhum_api.db.models import AuditEvent, DeletionState, User, UserRole

VALID_ROLES = {item.value for item in UserRole}


def _database_url(explicit: str | None) -> str:
    url = explicit or os.environ.get("MAFHUM_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if not url:
        raise SystemExit("set MAFHUM_DATABASE_URL or DATABASE_URL (no credentials on argv)")
    return url.replace("+psycopg", "")


def list_admins(url: str) -> list[dict[str, str]]:
    engine = create_engine(url)
    try:
        with Session(engine) as db:
            rows = db.scalars(
                select(User).where(User.role == UserRole.ADMIN.value).order_by(User.created_at)
            ).all()
            return [{"id": str(row.id), "email": row.email} for row in rows]
    finally:
        engine.dispose()


def set_role(url: str, email: str, role: str, *, force: bool = False) -> dict[str, str]:
    """Change one account's role and append an audit event in the same commit."""
    if role not in VALID_ROLES:
        raise SystemExit(f"role must be one of {sorted(VALID_ROLES)}")
    normalized = email.strip().lower()
    engine = create_engine(url)
    try:
        with Session(engine) as db:
            user = db.scalar(select(User).where(User.email == normalized))
            if user is None:
                raise SystemExit("no account with that email")
            previous = user.role
            if previous == UserRole.ADMIN.value and role != UserRole.ADMIN.value:
                active_admins = (
                    db.scalar(
                        select(func.count())
                        .select_from(User)
                        .where(
                            User.role == UserRole.ADMIN.value,
                            User.deletion_state == DeletionState.ACTIVE.value,
                        )
                    )
                    or 0
                )
                if active_admins <= 1 and not force:
                    raise SystemExit(
                        "refusing to demote the last active admin; use --force if intended"
                    )
            user.role = role
            db.add(
                AuditEvent(
                    actor_id=None,
                    action="admin_role_changed",
                    outcome="success",
                    target_type="user",
                    target_id=str(user.id),
                    details={"status": role},
                )
            )
            db.commit()
            return {
                "user_id": str(user.id),
                "previous_role": previous,
                "role": role,
                "audited": "true",
            }
    finally:
        engine.dispose()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--database-url", default=None)
    parser.add_argument("--email")
    parser.add_argument("--role", choices=sorted(VALID_ROLES))
    parser.add_argument("--list", action="store_true", help="list current admins")
    parser.add_argument("--force", action="store_true", help="allow demoting the last active admin")
    args = parser.parse_args(argv)
    url = _database_url(args.database_url)

    if args.list:
        print(json.dumps({"admins": list_admins(url)}, ensure_ascii=False, indent=2))
        return 0
    if not args.email or not args.role:
        parser.error("--email and --role are required unless --list is used")
    print(
        json.dumps(
            set_role(url, args.email, args.role, force=args.force),
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
