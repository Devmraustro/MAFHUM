"""Bounded, opt-in retention for the append-only audit log.

Deleting audit rows is a legal/business decision, not a maintenance default.
This command is DRY RUN unless ``--apply`` is given, and even then it only
removes ``audit_events`` rows older than the configured window, in bounded
batches. Nothing in the application calls it automatically.

Usage
-----
    # See what one run would remove (no writes):
    AUDIT_RETENTION_DAYS=365 python tools/purge_audit.py --days 365

    # Actually remove, after checking the legal-hold / retention policy:
    AUDIT_RETENTION_DAYS=365 python tools/purge_audit.py --days 365 --apply

The database URL comes from MAFHUM_DATABASE_URL / DATABASE_URL or the
application settings; it is never passed on the command line.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from mafhum_api.config import get_settings
from mafhum_api.db.base import Database
from mafhum_api.services.audit.service import AuditService


def main(argv: list[str] | None = None) -> int:
    settings = get_settings()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--days",
        type=int,
        default=settings.audit_retention_days,
        help="Retention window in days; 0 disables deletion (default: AUDIT_RETENTION_DAYS)",
    )
    parser.add_argument("--batch-size", type=int, default=settings.audit_purge_batch_size)
    parser.add_argument("--max-batches", type=int, default=settings.audit_purge_max_batches)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually delete. Without this flag the command only reports.",
    )
    args = parser.parse_args(argv)

    if args.days < 0 or args.batch_size < 1 or args.max_batches < 1:
        parser.error("days must be >= 0 and batch-size/max-batches must be >= 1")

    if args.days == 0:
        print(json.dumps({"status": "retention_disabled", "deleted": 0}, indent=2))
        return 0

    if not args.apply:
        print(
            "DRY RUN — no rows will be deleted. Confirm the retention policy and "
            "any legal hold, then re-run with --apply.",
            file=sys.stderr,
        )

    database = Database(
        settings.database_url,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
    )
    try:
        result = AuditService().prune(
            database,
            retention_days=args.days,
            batch_size=args.batch_size,
            max_batches=args.max_batches,
            dry_run=not args.apply,
        )
    finally:
        database.dispose()
    print(json.dumps(result.as_dict(), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
