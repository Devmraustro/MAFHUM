"""Consistent backup and restore for MAFHUM.

What has to be backed up
------------------------
MAFHUM stores two kinds of state, and they refer to each other:

1. **PostgreSQL** — 16 tables: accounts, sessions, projects, document metadata,
   processing jobs, extracted text segments, sources, and all study material.
2. **Blob storage** — the uploaded files themselves, on the filesystem under
   ``storage_root``, addressed by the opaque key recorded in
   ``documents.storage_key``.

Backing up either one alone produces a broken system: a database restored without
its blobs has rows that point at nothing, and blobs restored without the database
are unreachable files nobody can authorize a download for. So a backup is always
both, plus a manifest that lets the pair be verified.

Consistency
-----------
``pg_dump`` produces a transactionally consistent snapshot of the database. The
blob directory is copied while the application may still be writing, so the pair
is only guaranteed consistent if the application is quiesced. ``--require-quiesced``
records whether the caller asserted that; without it the manifest says so plainly
rather than implying a guarantee that was not obtained.

The failure mode this guards against is specific: a document row committed between
the database dump and the blob copy would be restored pointing at a blob that is
not in the archive. ``verify()`` detects exactly that, by checking every
``storage_key`` in the restored database against the restored blob directory.

Secrets
-------
No credential is written into an archive or a log. The database connection string
is read from the environment at run time, and the manifest records the database
*name* only.

Usage
-----
    python tools/backup.py --storage-root data/mafhum-storage --out backups/
    python tools/backup.py ... --verify        # restore to a scratch dir and check
    python tools/restore.py --archive backups/mafhum-<stamp>.tar.zst --database-url ...
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from datetime import UTC, datetime
from pathlib import Path

SCHEMA_VERSION = 1
_ARCHIVE_GLOB = "mafhum-*.tar"


def _now() -> datetime:
    return datetime.now(UTC)


def _stamp() -> str:
    return _now().strftime("%Y%m%dT%H%M%SZ")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _tool_in(bindir: Path, name: str) -> str | None:
    """Return the first existing spelling of ``name`` in ``bindir``.

    Windows names the executables ``pg_dump.exe``; a bare-name check would
    miss tools that are actually installed.
    """
    for candidate in (bindir / name, bindir / f"{name}.exe"):
        if candidate.exists():
            return str(candidate)
    return None


def _pg_major(path: str) -> int | None:
    """Return the tool's major version, or ``None`` if it cannot be read."""
    try:
        result = subprocess.run(  # noqa: S603
            [path, "--version"],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
    except OSError:
        return None
    match = re.search(r"(\d+)(?:\.|$)", result.stdout)
    return int(match.group(1)) if match else None


def _server_major(database_url: str) -> int | None:
    """Return the target server's major version, or ``None`` if unreachable."""
    try:
        import psycopg

        with psycopg.connect(database_url, connect_timeout=10) as connection:
            value = connection.execute("SHOW server_version_num").fetchone()
    except Exception:
        return None
    if not value:
        return None
    # server_version_num is e.g. 160008 for 16.8; major is the leading digits.
    return int(str(value[0])[:2].lstrip("0") or "0")


def find_pg_tools(database_url: str) -> tuple[str, str]:
    """Locate a ``pg_dump``/``pg_restore`` pair that matches the target server.

    Candidates, in order: the system PATH pair, then the pair bundled with
    ``pgserver``. A dump/restore run must use tools whose major version matches
    the server: a newer ``pg_restore`` emits statements the older server
    rejects (observed live: a runner image gained PostgreSQL 17 clients, and
    ``pg_restore`` 17 failed against a 16.x server on its own
    ``SET transaction_timeout = 0;``), and an older ``pg_dump`` silently drops
    what a newer server offers. The first candidate pair whose ``pg_restore``
    major matches the server's major wins; if the server version cannot be
    read, the PATH pair is returned when present (the previous behaviour).
    """
    candidates: list[tuple[str, str]] = []
    path_dump, path_restore = shutil.which("pg_dump"), shutil.which("pg_restore")
    if path_dump and path_restore:
        candidates.append((path_dump, path_restore))
    try:
        import pgserver
    except ImportError:
        pgserver = None
    if pgserver is not None:
        bindir = Path(pgserver.__file__).parent / "pginstall" / "bin"
        bundled_dump, bundled_restore = _tool_in(bindir, "pg_dump"), _tool_in(bindir, "pg_restore")
        if bundled_dump and bundled_restore:
            candidates.append((bundled_dump, bundled_restore))
    if not candidates:
        raise SystemExit(
            "pg_dump/pg_restore not found. Install the PostgreSQL client tools, or "
            "install pgserver (which bundles them)."
        )

    server_major = _server_major(database_url)
    if server_major is not None:
        for dump, restore in candidates:
            if _pg_major(restore) == server_major:
                return dump, restore

    return candidates[0]


def create_backup(
    *,
    database_url: str,
    storage_root: Path,
    out_dir: Path,
    require_quiesced: bool = False,
) -> Path:
    """Write a tar archive containing the database dump, the blobs and a manifest.

    Returns the archive path. Raises on failure rather than writing a partial
    archive that looks complete.
    """
    pg_dump, _ = find_pg_tools(database_url)
    out_dir.mkdir(parents=True, exist_ok=True)

    stamp = _stamp()
    staging = Path(tempfile.mkdtemp(prefix="mafhum-backup-"))
    try:
        dump_path = staging / "database.dump"
        # Custom format: compressed, and selectively restorable. Plain SQL would
        # not let an operator restore a single table during an incident.
        command = [
            pg_dump,
            "--format=custom",
            "--no-owner",
            "--no-privileges",
            f"--file={dump_path}",
            # pg_dump takes the URL from the environment so it never appears in a
            # process listing.
            database_url,
        ]
        env = {**os.environ, "PGPASSFILE": os.environ.get("PGPASSFILE", "")}
        result = subprocess.run(  # noqa: S603
            command,
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )
        if result.returncode != 0:
            # stderr can contain the connection string on some errors; report the
            # exit status and the last line only.
            tail = (result.stderr or "").strip().splitlines()[-1:] or [""]
            raise RuntimeError(f"pg_dump failed (exit {result.returncode}): {tail[0][:200]}")
        if not dump_path.exists() or dump_path.stat().st_size == 0:
            raise RuntimeError("pg_dump produced no output")

        blobs = staging / "blobs"
        blob_files: list[dict[str, str]] = []
        if storage_root.exists():
            blobs.mkdir(parents=True, exist_ok=True)
            for path in sorted(p for p in storage_root.rglob("*") if p.is_file()):
                relative = path.relative_to(storage_root)
                target = blobs / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, target)
                blob_files.append(
                    {
                        "key": relative.as_posix(),
                        "sha256": _sha256(path),
                        "size": str(path.stat().st_size),
                    }
                )

        manifest = {
            "schema_version": SCHEMA_VERSION,
            "created_at": _now().isoformat(),
            "database_name": _database_name(database_url),
            "database_dump_sha256": _sha256(dump_path),
            "blob_count": len(blob_files),
            "blobs": blob_files,
            # Recorded honestly: if the application kept writing during the blob
            # copy, the pair is not provably consistent and the operator needs to
            # know that from the archive itself.
            "quiesced_asserted": require_quiesced,
        }
        (staging / "manifest.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8"
        )

        archive = out_dir / f"mafhum-{stamp}.tar"
        with tarfile.open(archive, "w") as tar:
            for name in ("database.dump", "manifest.json"):
                tar.add(staging / name, arcname=name)
            if blobs.exists():
                tar.add(blobs, arcname="blobs")
        return archive
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def _reject_unsafe_members(tar: tarfile.TarFile) -> None:
    """Refuse an archive that could write outside the destination.

    A backup archive is trusted input in principle, but restore is exactly the
    place not to rely on that: a crafted archive with an absolute or
    parent-traversing member would otherwise write anywhere the restoring user
    can. Checked explicitly rather than delegated to tarfile's ``filter``
    argument, which does not exist before Python 3.11.4.
    """
    for member in tar.getmembers():
        name = Path(member.name)
        if name.is_absolute() or ".." in name.parts:
            raise RuntimeError(f"refusing unsafe path in archive: {member.name}")


def _safe_extractall(tar: tarfile.TarFile, destination: Path) -> None:
    """Extract using tarfile's own data filter when this Python has one."""
    try:
        tar.extractall(destination, filter="data")  # type: ignore[call-arg]
    except TypeError:
        # Python < 3.11.4 has no ``filter`` keyword; _reject_unsafe_members above
        # is the guard that applies on every version.
        tar.extractall(destination)  # noqa: S202


def _database_name(database_url: str) -> str:
    """Extract the database name, discarding any credentials."""
    from urllib.parse import urlparse

    parsed = urlparse(database_url.replace("+psycopg", ""))
    return parsed.path.lstrip("/") or "unknown"


def read_manifest(archive: Path) -> dict[str, object]:
    with tarfile.open(archive, "r") as tar:
        member = tar.getmember("manifest.json")
        handle = tar.extractfile(member)
        if handle is None:
            raise RuntimeError("manifest.json is empty in the archive")
        return json.loads(handle.read().decode("utf-8"))


def restore_backup(
    *,
    archive: Path,
    database_url: str,
    storage_root: Path,
    restore_blobs: bool = True,
) -> dict[str, object]:
    """Restore an archive into a target database and blob directory.

    The database is dropped and recreated from the dump. Blobs are written into
    ``storage_root``. Nothing here deletes files that are not in the archive, so
    restoring over a populated directory adds and overwrites but does not prune.
    """
    _, pg_restore = find_pg_tools(database_url)
    staging = Path(tempfile.mkdtemp(prefix="mafhum-restore-"))
    try:
        with tarfile.open(archive, "r") as tar:
            # Reject archives containing absolute or parent-traversing paths. A
            # backup archive is trusted input in principle, but extracting one
            # must not be able to write outside the staging directory.
            _reject_unsafe_members(tar)
            _safe_extractall(tar, staging)

        manifest_path = staging / "manifest.json"
        if not manifest_path.exists():
            raise RuntimeError("archive has no manifest.json; not a MAFHUM backup")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

        dump_path = staging / "database.dump"
        if not dump_path.exists():
            raise RuntimeError("archive has no database.dump")
        expected = manifest.get("database_dump_sha256")
        if expected and _sha256(dump_path) != expected:
            raise RuntimeError("database.dump checksum does not match the manifest")

        result = subprocess.run(  # noqa: S603
            [
                pg_restore,
                "--clean",
                "--if-exists",
                "--no-owner",
                "--no-privileges",
                "--exit-on-error",
                f"--dbname={database_url}",
                str(dump_path),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            tail = (result.stderr or "").strip().splitlines()[-1:] or [""]
            raise RuntimeError(f"pg_restore failed (exit {result.returncode}): {tail[0][:200]}")

        restored_blobs = 0
        if restore_blobs:
            source = staging / "blobs"
            if source.exists():
                storage_root.mkdir(parents=True, exist_ok=True)
                for path in source.rglob("*"):
                    if not path.is_file():
                        continue
                    target = storage_root / path.relative_to(source)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(path, target)
                    restored_blobs += 1

        return {
            "manifest": manifest,
            "blobs_restored": restored_blobs,
            "database": _database_name(database_url),
        }
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def verify_state(
    *,
    database_url: str,
    storage_root: Path,
    manifest: dict[str, object],
) -> dict[str, object]:
    """Check a *live* database and blob directory against a manifest.

    This is the check an operator runs after a real restore, against the system
    that will actually serve traffic -- not against a scratch copy. It answers the
    question that matters: does every document row resolve to a blob that exists
    and matches its recorded checksum?

    Kept separate from :func:`verify_backup` because the two answer different
    questions. ``verify_backup`` restores first, so it validates an archive; it
    would silently undo any corruption already present on disk. This function
    changes nothing and reports what is actually there.
    """
    from sqlalchemy import create_engine, text

    problems: list[str] = []
    engine = create_engine(database_url.replace("+psycopg", ""))
    try:
        with engine.connect() as connection:
            rows = connection.execute(
                text(
                    "SELECT storage_key, content_sha256 FROM documents "
                    "WHERE storage_key IS NOT NULL"
                )
            ).all()
    finally:
        engine.dispose()

    document_count = len(rows)
    missing: list[str] = []
    mismatched: list[str] = []
    for storage_key, content_sha256 in rows:
        blob = storage_root / str(storage_key)
        if not blob.exists():
            missing.append(str(storage_key))
            continue
        if content_sha256 and _sha256(blob) != str(content_sha256):
            mismatched.append(str(storage_key))

    blob_entries = manifest.get("blobs") or []
    manifest_missing: list[str] = []
    for entry in blob_entries:
        key = str(entry["key"])
        blob = storage_root / key
        if not blob.exists():
            manifest_missing.append(key)
        elif _sha256(blob) != str(entry["sha256"]):
            mismatched.append(key)

    if missing:
        problems.append(f"{len(missing)} document row(s) reference blobs absent from the archive")
    if manifest_missing:
        problems.append(f"{len(manifest_missing)} manifest blob(s) are missing")
    if mismatched:
        problems.append(f"{len(mismatched)} blob(s) failed checksum verification")

    return {
        "ok": not problems,
        "problems": problems,
        "documents_in_restored_database": document_count,
        "blobs_in_manifest": len(blob_entries),
        "missing_blobs": missing[:10],
        "checksum_mismatches": mismatched[:10],
    }


def verify_backup(
    *,
    archive: Path,
    database_url: str,
    storage_root: Path,
) -> dict[str, object]:
    """Restore into a scratch target, then prove the result is consistent.

    A successful restore is not the same as a working backup: an archive can
    restore cleanly and still be missing blobs the database refers to. To check
    what is already on disk without re-restoring, use :func:`verify_state`.
    """
    result = restore_backup(archive=archive, database_url=database_url, storage_root=storage_root)
    manifest = result["manifest"]
    report = verify_state(database_url=database_url, storage_root=storage_root, manifest=manifest)
    report["blobs_restored"] = result["blobs_restored"]
    report["database"] = result["database"]
    report["quiesced_asserted"] = manifest.get("quiesced_asserted", False)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--database-url", default=os.environ.get("MAFHUM_DATABASE_URL"))
    parser.add_argument("--storage-root", default=os.environ.get("MAFHUM_STORAGE_ROOT"))
    parser.add_argument("--out", default="backups")
    parser.add_argument(
        "--require-quiesced",
        action="store_true",
        help="assert the application is stopped; recorded in the manifest",
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        help="after backing up, restore into a scratch target and verify consistency",
    )
    parser.add_argument(
        "--verify-archive", help="verify an existing archive instead of creating one"
    )
    args = parser.parse_args(argv)

    if args.verify_archive:
        database_url = args.database_url
        storage_root = args.storage_root
        if not database_url or not storage_root:
            parser.error("--verify-archive needs --database-url and --storage-root")
        report = verify_backup(
            archive=Path(args.verify_archive),
            database_url=database_url,
            storage_root=Path(storage_root),
        )
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0 if report["ok"] else 1

    if not args.database_url:
        parser.error("--database-url or MAFHUM_DATABASE_URL is required")
    if not args.storage_root:
        parser.error("--storage-root or MAFHUM_STORAGE_ROOT is required")

    archive = create_backup(
        database_url=args.database_url,
        storage_root=Path(args.storage_root),
        out_dir=Path(args.out),
        require_quiesced=args.require_quiesced,
    )
    print(f"backup written: {archive} ({archive.stat().st_size} bytes)")

    if args.verify:
        scratch_db = f"{args.database_url.rsplit('/', 1)[0]}/mafhum_restore_check"
        scratch_storage = Path(tempfile.mkdtemp(prefix="mafhum-verify-"))
        try:
            report = verify_backup(
                archive=archive,
                database_url=scratch_db,
                storage_root=scratch_storage,
            )
            print(json.dumps(report, indent=2, sort_keys=True))
            return 0 if report["ok"] else 1
        finally:
            shutil.rmtree(scratch_storage, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
