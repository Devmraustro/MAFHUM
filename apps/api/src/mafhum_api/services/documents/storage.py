"""Private blob storage.

Contract
--------
* Keys are opaque and server-generated (``shard/uuid4hex``). Callers never
  supply a key, and a key never embeds a user-supplied filename.
* ``resolve`` refuses any key that does not round-trip to a path strictly inside
  the storage root, so a crafted key cannot traverse or follow out of the tree.
* Writes go through a temporary file in the same directory and are published
  with ``os.replace`` (atomic on POSIX), so a crash never leaves a half-written
  object at its final key.
* The backend returns a ``StorageObject`` handle; callers open a *stream*, not a
  whole-file ``bytes`` blob, so large documents never have to fit in memory.

The interface is deliberately provider-agnostic: an S3-compatible backend could
implement the same protocol without touching document business logic.
"""

from __future__ import annotations

import logging
import os
import secrets
import shutil
import stat
from collections.abc import Iterable, Iterator, Sequence
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

logger = logging.getLogger(__name__)

_CHUNK = 1024 * 256
# 32 bytes of entropy => 2^256 keys; unguessable without DB access.
_KEY_BYTES = 32


class StorageError(Exception):
    """Internal storage failure. Never carries a path to the API layer."""


class StorageKeyError(StorageError):
    """The requested key is not a valid, in-tree key."""


class StorageObjectNotFoundError(StorageError):
    """No object exists for this key."""


@dataclass(frozen=True, slots=True)
class StorageObject:
    """A stored object plus the metadata callers may expose."""

    key: str
    size_bytes: int


@runtime_checkable
class DocumentStorage(Protocol):
    """Storage interface used by the ingestion pipeline."""

    def generate_key(self) -> str: ...

    def write(self, key: str, chunks: Iterable[bytes], *, max_bytes: int) -> StorageObject: ...

    def open_read(self, key: str) -> Iterator[bytes]: ...

    def exists(self, key: str) -> bool: ...

    def stat(self, key: str) -> StorageObject: ...

    def delete(self, key: str) -> bool: ...

    def delete_many(self, keys: Sequence[str]) -> tuple[list[str], list[str]]: ...

    def reconcile_orphans(self, live_keys: Sequence[str]) -> list[str]: ...


def new_storage_key() -> str:
    """Return an opaque storage key with a two-character sharding prefix."""
    raw = secrets.token_hex(_KEY_BYTES)
    return f"{raw[:2]}/{raw}"


class LocalFileStorage:
    """Filesystem backend for zero-cost local development.

    Files live under a private root (default ``data/mafhum-storage``) with mode
    ``0700`` for directories and ``0600`` for objects. The root must not be a
    static/public directory; the API never serves files from disk directly and
    only streams them after an authorization check.
    """

    def __init__(
        self,
        root: Path | str,
        *,
        dir_mode: int = 0o700,
        file_mode: int = 0o600,
    ) -> None:
        self._root = Path(root).resolve()
        self._dir_mode = dir_mode
        self._file_mode = file_mode
        self._root.mkdir(parents=True, exist_ok=True)
        self._chmod(self._root, dir_mode)

    # --- key handling --------------------------------------------------------
    @property
    def root(self) -> Path:
        return self._root

    def generate_key(self) -> str:
        return new_storage_key()

    def resolve(self, key: str) -> Path:
        """Map a key to an in-tree absolute path, rejecting anything unsafe."""
        if not isinstance(key, str) or not key or len(key) > 128:
            raise StorageKeyError("invalid storage key")
        if "\x00" in key or "\\" in key:
            raise StorageKeyError("invalid storage key")
        # Split the raw string rather than using Path.parts: pathlib silently
        # drops "." segments, which would let "aa/." resolve to a directory.
        raw_parts = key.split("/")
        if not raw_parts or any(part in {"", ".", ".."} for part in raw_parts):
            raise StorageKeyError("invalid storage key")
        if any(part.startswith("/") or part != part.strip() for part in raw_parts):
            raise StorageKeyError("invalid storage key")
        parts = tuple(raw_parts)
        candidate = (self._root.joinpath(*parts)).resolve()
        if candidate == self._root or self._root not in candidate.parents:
            raise StorageKeyError("storage key escapes storage root")
        # Reject symlinked components: an attacker with any write access could
        # otherwise redirect a key onto an arbitrary file.
        for part_path in _ancestors_within(candidate, self._root):
            if part_path.is_symlink():
                raise StorageKeyError("storage key traverses a symlink")
        return candidate

    # --- IO ------------------------------------------------------------------
    def write(self, key: str, chunks: Iterable[bytes], *, max_bytes: int) -> StorageObject:
        """Atomically write ``chunks`` under ``key``, enforcing ``max_bytes``."""
        target = self.resolve(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        # `mkdir(parents=True)` creates any *missing ancestor* with the default
        # umask, so a storage root removed while the process is running would
        # come back group/world-readable (0o755 under a 0o022 umask). Re-assert
        # the mode on the shard *and* the root instead of trusting the mode the
        # directory happened to be created with.
        self._chmod(target.parent, self._dir_mode)
        self._chmod(self._root, self._dir_mode)

        written = 0
        # O_EXCL guarantees we never clobber an existing object (e.g. another
        # user's file) even if a key were somehow reused.
        tmp_path = target.with_name(f".tmp-{secrets.token_hex(8)}")
        try:
            fd = os.open(tmp_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, self._file_mode)
        except OSError as exc:
            raise StorageError("could not create temporary upload file") from exc
        try:
            with os.fdopen(fd, "wb") as handle:
                for chunk in chunks:
                    if not chunk:
                        continue
                    written += len(chunk)
                    if written > max_bytes:
                        raise StorageError("upload exceeds maximum size")
                    handle.write(chunk)
                handle.flush()
                os.fsync(handle.fileno())
            tmp_path.chmod(self._file_mode)
            tmp_path.replace(target)
        except BaseException:
            # Includes KeyboardInterrupt/SystemExit: never leave a partial file.
            _unlink_quietly(tmp_path)
            raise
        return StorageObject(key=key, size_bytes=written)

    def open_read(self, key: str) -> Iterator[bytes]:
        target = self.resolve(key)
        if not target.is_file():
            raise StorageObjectNotFoundError("object not found")
        return _iter_file(target, _CHUNK)

    def read_bytes(self, key: str, *, max_bytes: int) -> bytes:
        """Bounded read for small objects. Raises rather than over-reading."""
        buf = bytearray()
        for chunk in self.open_read(key):
            buf.extend(chunk)
            if len(buf) > max_bytes:
                raise StorageError("object exceeds bounded read limit")
        return bytes(buf)

    def exists(self, key: str) -> bool:
        try:
            return self.resolve(key).is_file()
        except StorageKeyError:
            return False

    def stat(self, key: str) -> StorageObject:
        target = self.resolve(key)
        if not target.is_file():
            raise StorageObjectNotFoundError("object not found")
        return StorageObject(key=key, size_bytes=target.stat().st_size)

    def delete(self, key: str) -> bool:
        """Delete one object. Returns False when it was already absent."""
        try:
            target = self.resolve(key)
        except StorageKeyError:
            logger.warning("refused to delete invalid storage key", extra={"event": "storage"})
            return False
        try:
            target.unlink()
        except FileNotFoundError:
            return False
        except OSError as exc:
            raise StorageError("could not delete stored object") from exc
        _prune_empty_dirs(target.parent, self._root)
        return True

    def delete_many(self, keys: Sequence[str]) -> tuple[list[str], list[str]]:
        """Delete many objects, continuing past failures.

        Returns ``(deleted, failed)`` so callers can record which keys still need
        cleanup instead of silently pretending deletion succeeded.
        """
        deleted: list[str] = []
        failed: list[str] = []
        for key in keys:
            try:
                if self.delete(key):
                    deleted.append(key)
            except StorageError:
                logger.error(
                    "storage deletion failed; key retained for reconciliation",
                    extra={"event": "storage_delete_failed"},
                )
                failed.append(key)
        return deleted, failed

    def purge_all(self) -> int:
        """Remove every object under the root (test/teardown helper)."""
        if not self._root.exists():
            return 0
        removed = sum(1 for _ in self._root.rglob("*") if _.is_file())
        shutil.rmtree(self._root, ignore_errors=True)
        self._root.mkdir(parents=True, exist_ok=True)
        self._chmod(self._root, self._dir_mode)
        return removed

    def reconcile_orphans(self, live_keys: Sequence[str]) -> list[str]:
        """Return keys present on disk but absent from ``live_keys``.

        Used by the cleanup job to find objects orphaned by a crash between the
        file write and the database commit. It reports only; deletion is a
        separate, explicit step.
        """
        live = set(live_keys)
        orphans: list[str] = []
        for path in self._root.rglob("*"):
            if not path.is_file() or path.name.startswith(".tmp-"):
                continue
            try:
                key = str(path.relative_to(self._root))
            except ValueError:
                continue
            if key not in live:
                orphans.append(key)
        return orphans

    @staticmethod
    def _chmod(path: Path, mode: int) -> None:
        try:
            path.chmod(mode)
        except OSError:
            # Best-effort: umask/filesystem may not permit it. Not fatal.
            logger.debug("could not set permissions on storage path", extra={"event": "storage"})


def _iter_file(path: Path, chunk_size: int) -> Iterator[bytes]:
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(chunk_size)
            if not chunk:
                return
            yield chunk


def _unlink_quietly(path: Path) -> None:
    with suppress(OSError):
        path.unlink(missing_ok=True)


def _ancestors_within(candidate: Path, root: Path) -> Iterator[Path]:
    current = candidate.parent
    while current != root and root in current.parents:
        yield current
        current = current.parent


def _prune_empty_dirs(directory: Path, root: Path) -> None:
    current = directory
    while current != root and root in current.parents:
        try:
            current.rmdir()
        except OSError:
            return
        current = current.parent


def file_mode_is_restrictive(path: Path, expected: int) -> bool:
    """True when ``path`` has no group/other permission bits set."""
    try:
        mode = stat.S_IMODE(path.stat().st_mode)
    except OSError:
        return False
    return mode == expected and not (mode & 0o077)
