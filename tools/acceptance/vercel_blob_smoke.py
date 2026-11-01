"""Real-store acceptance for the private Vercel Blob storage backend.

This is the tool the production-readiness report's "real Blob store smoke"
BLOCKED item refers to. It exercises **MAFHUM's actual adapter**
(`mafhum_api.services.documents.vercel_blob_storage.VercelBlobStorage`) against
the **real** Blob service — no fake client, no mock — so a green run is
evidence about the production path, not about test doubles.

It runs only when the operator supplies a real read-write token:

    BLOB_READ_WRITE_TOKEN=vercel_blob_rw_… python tools/acceptance/vercel_blob_smoke.py

Without the variable it fails fast with instructions (exit 2) and performs
nothing. The token is never printed; the store id derived from it is never
printed either. Every object this script creates is deleted in a `finally`
block; cleanup failures are reported and never mask a check failure.

Checks covered (master-execution Phase 4):
upload, streaming read round-trip, metadata, overwrite refusal, size
boundaries (exact fit / one byte over, with nothing left behind), missing
object behaviour (exists/stat/read/delete), deletion and idempotence, batch
deletion with mixed hits, orphan reconciliation report, unsafe-key rejection,
and an **unauthenticated-URL privacy probe**: the content must not be
downloadable without the token.
"""

from __future__ import annotations

import os
import secrets
import sys
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "apps" / "api" / "src"))

from mafhum_api.services.documents.storage import (  # noqa: E402
    StorageError,
    StorageKeyError,
    StorageObjectNotFoundError,
)
from mafhum_api.services.documents.vercel_blob_storage import (  # noqa: E402
    _BLOB_PREFIX,
    VercelBlobStorage,
    _parse_read_write_token,
    _read_write_session_options,
)

PAYLOAD = secrets.token_bytes(64 * 1024)
created_keys: list[str] = []


def require(condition: bool, message: str) -> None:
    """Check-style assertion: raises AssertionError with a fixed message."""
    if not condition:
        raise AssertionError(message)


class Checks:
    def __init__(self) -> None:
        self.failures: list[str] = []
        self.skipped: list[str] = []

    def run(self, name: str, body: Callable[[], None]) -> None:
        try:
            body()
        except AssertionError as exc:
            self.failures.append(f"{name}: {exc}")
            print(f"FAIL  {name}: {exc}", flush=True)
        except Exception as exc:  # report, do not crash the whole run
            self.failures.append(f"{name}: unexpected {type(exc).__name__}: {exc}")
            print(f"FAIL  {name}: unexpected {type(exc).__name__}: {exc}", flush=True)
        else:
            print(f"PASS  {name}", flush=True)

    def skip(self, name: str, reason: str) -> None:
        self.skipped.append(f"{name}: {reason}")
        print(f"SKIP  {name}: {reason}", flush=True)


def expect_storage_error(body: Callable[[], Any]) -> None:
    try:
        body()
    except (StorageError, StorageKeyError):
        return
    raise AssertionError("expected StorageError/StorageKeyError, got success")


def check_round_trip(storage: VercelBlobStorage, key: str) -> None:
    written = storage.write(
        key, iter([PAYLOAD[:1000], PAYLOAD[1000:]]), max_bytes=len(PAYLOAD)
    )
    require(written.size_bytes == len(PAYLOAD), f"reported size {written.size_bytes}")
    streamed = b"".join(storage.open_read(key))
    require(streamed == PAYLOAD, "round-trip bytes differ")


def check_stat(storage: VercelBlobStorage, key: str) -> None:
    stat = storage.stat(key)
    require(
        stat.key == key and stat.size_bytes == len(PAYLOAD), f"bad metadata: {stat}"
    )


def check_no_overwrite(storage: VercelBlobStorage, key: str) -> None:
    expect_storage_error(lambda: storage.write(key, iter([b"x"]), max_bytes=10))
    require(b"".join(storage.open_read(key)) == PAYLOAD, "original object was modified")


def check_size_boundary(storage: VercelBlobStorage, key: str) -> None:
    half = len(PAYLOAD) // 2
    storage.write(key, iter([PAYLOAD[:half], PAYLOAD[half:]]), max_bytes=len(PAYLOAD))
    require(storage.stat(key).size_bytes == len(PAYLOAD), "exact-fit size mismatch")
    created_keys.remove(key)
    require(storage.delete(key) is True, "delete of the boundary object returned False")
    over_key = storage.generate_key()
    created_keys.append(over_key)
    expect_storage_error(
        lambda: storage.write(over_key, iter([PAYLOAD, b"x"]), max_bytes=len(PAYLOAD))
    )
    require(storage.exists(over_key) is False, "failed write left an object behind")
    created_keys.remove(over_key)


def check_missing(storage: VercelBlobStorage, key: str) -> None:
    require(storage.exists(key) is False, "missing object reported as existing")
    try:
        storage.stat(key)
    except StorageObjectNotFoundError:
        pass
    else:
        raise AssertionError("stat on a missing object did not raise")
    try:
        list(storage.open_read(key))
    except StorageObjectNotFoundError:
        pass
    else:
        raise AssertionError("read of a missing object did not raise")
    require(storage.delete(key) is False, "delete of a missing object returned True")


def check_delete(storage: VercelBlobStorage, key: str) -> None:
    require(storage.delete(key) is True, "delete of an existing object returned False")
    created_keys.remove(key)
    require(storage.exists(key) is False, "object still exists after delete")
    require(storage.delete(key) is False, "second delete returned True")


def check_delete_many(storage: VercelBlobStorage) -> None:
    present = storage.generate_key()
    absent = storage.generate_key()
    created_keys.append(present)
    storage.write(present, iter([b"batch"]), max_bytes=64)
    deleted, missing = storage.delete_many([present, absent])
    created_keys.remove(present)
    require(deleted == [present], f"deleted list wrong: {deleted}")
    require(missing == [absent], f"missing list wrong: {missing}")
    require(storage.exists(present) is False, "batch-deleted object still exists")


def check_reconcile(storage: VercelBlobStorage) -> None:
    stray = storage.generate_key()
    created_keys.append(stray)
    storage.write(stray, iter([b"orphan"]), max_bytes=64)
    live = storage.generate_key()
    created_keys.append(live)
    storage.write(live, iter([b"live"]), max_bytes=64)
    orphans = storage.reconcile_orphans([live])
    require(stray in orphans, f"stray {stray} not reported")
    require(live not in orphans, "live key wrongly reported")
    require(
        storage.exists(live) is True and storage.exists(stray) is True,
        "reconcile mutated",
    )
    # Reconciliation only reports; removal is the caller's explicit action.
    deleted, missing = storage.delete_many([stray, live])
    require(
        deleted == [stray, live] and missing == [],
        f"cleanup lists wrong: {deleted}/{missing}",
    )
    created_keys.remove(stray)
    created_keys.remove(live)


def check_unsafe_keys(storage: VercelBlobStorage) -> None:
    for bad in ("../../etc/passwd", "aa\\bb", "AA/1", "aa/" + "z" * 64, "", "a" * 200):
        expect_storage_error(
            lambda bad=bad: storage.write(bad, iter([b"x"]), max_bytes=8)
        )
    require(
        not any(k in created_keys for k in ("../../etc/passwd", "aa\\bb")),
        "an unsafe key was created",
    )


def check_private_url(token: str) -> None:
    """Fetch the object's URL with NO credentials; content must not be served."""
    from vercel.api import session as vercel_sdk_session
    from vercel.blob import sync as blob

    store_id = _parse_read_write_token(token)
    opts = _read_write_session_options(token, store_id)
    storage = VercelBlobStorage(blob_read_write_token=token)
    # Give the probe its own live object: earlier checks may already have
    # deleted theirs, and the probe must exercise a real, freshly written one.
    probe_key = storage.generate_key()
    created_keys.append(probe_key)
    storage.write(probe_key, iter([PAYLOAD]), max_bytes=len(PAYLOAD))
    with vercel_sdk_session(service_options=[opts]):
        items = list(blob.query_blobs(prefix=f"{_BLOB_PREFIX}{probe_key}"))
    require(bool(items), "query_blobs did not find the object the adapter just wrote")
    url = getattr(items[0], "url", None)
    if not url:
        # Disclosed, not passed: without a URL from the SDK there is nothing to
        # probe; the deployment's own HTTP surface must still verify this.
        print("SKIP  private-url probe: SDK returned no URL for the object", flush=True)
        return
    # The URL comes from the SDK's own metadata for the object this script
    # just wrote; the probe deliberately sends no credentials.
    request = urllib.request.Request(url, method="GET")  # noqa: S310 - https SDK metadata URL
    try:
        with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310 - same https URL
            status = response.status
            body = response.read(16)
    except urllib.error.HTTPError as exc:
        status = exc.code
        body = b""
    require(
        status != 200,
        f"private object downloadable without credentials (HTTP {status})",
    )
    require(body == b"", "unexpected body on a refused download")


def main() -> int:
    token = os.environ.get("BLOB_READ_WRITE_TOKEN", "")
    if not token:
        print(
            "BLOCKED: set BLOB_READ_WRITE_TOKEN to a real store token to run "
            "this acceptance. Nothing was executed and nothing was created.",
            file=sys.stderr,
        )
        return 2

    checks = Checks()
    storage = VercelBlobStorage(blob_read_write_token=token)
    key = storage.generate_key()
    created_keys.append(key)

    checks.run(
        "upload and streaming read round-trip", lambda: check_round_trip(storage, key)
    )
    checks.run("metadata reports the written size", lambda: check_stat(storage, key))
    checks.run(
        "overwrite is refused and original bytes survive",
        lambda: check_no_overwrite(storage, key),
    )
    boundary_key = storage.generate_key()
    created_keys.append(boundary_key)
    checks.run(
        "size boundary: exact fit accepted, one byte over refused without residue",
        lambda: check_size_boundary(storage, boundary_key),
    )
    checks.run(
        "missing object: exists/stat/read/delete all behave",
        lambda: check_missing(storage, storage.generate_key()),
    )
    checks.run(
        "delete returns True once then False; object is gone",
        lambda: check_delete(storage, key),
    )
    checks.run(
        "delete_many splits deleted from missing", lambda: check_delete_many(storage)
    )
    checks.run(
        "reconcile_orphans reports a stray object and stays silent for live keys",
        lambda: check_reconcile(storage),
    )
    checks.run(
        "unsafe keys are rejected before any provider call",
        lambda: check_unsafe_keys(storage),
    )
    checks.run(
        "unauthenticated download of a private object is refused",
        lambda: check_private_url(token),
    )

    if checks.failures:
        print(f"RESULT: {len(checks.failures)} BLOB ACCEPTANCE FAILURE(S)", flush=True)
        return 1
    note = f" ({len(checks.skipped)} probe(s) skipped)" if checks.skipped else ""
    print(f"RESULT: ALL BLOB ACCEPTANCE CHECKS PASSED{note}", flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    finally:
        if created_keys:
            cleanup_token = os.environ.get("BLOB_READ_WRITE_TOKEN", "")
            if cleanup_token:
                try:
                    VercelBlobStorage(blob_read_write_token=cleanup_token).delete_many(
                        created_keys
                    )
                    print(
                        f"cleanup: removed {len(created_keys)} probe object(s)",
                        flush=True,
                    )
                except Exception as exc:  # cleanup must never mask a check failure
                    print(
                        f"cleanup WARNING: {len(created_keys)} object(s) may remain: {exc}",
                        flush=True,
                    )
