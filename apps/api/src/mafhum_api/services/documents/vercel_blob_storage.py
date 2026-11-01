"""Vercel private Blob backend implementing the existing synchronous contract.

Uploads are bounded and streamed from a SpooledTemporaryFile. The SDK requires
an exact content_length for streaming sources; the spool is transient only and
never acts as the durable store.
"""

from __future__ import annotations

import logging
import re
import tempfile
from collections.abc import Iterable, Iterator, Sequence
from contextlib import suppress
from typing import Protocol, cast

from mafhum_api.services.documents.storage import (
    StorageError,
    StorageKeyError,
    StorageObject,
    StorageObjectNotFoundError,
)

logger = logging.getLogger(__name__)


def _is_blob_not_found(exc: Exception) -> bool:
    """Recognize the SDK's documented not-found exception without leaking it."""
    return isinstance(exc, FileNotFoundError) or type(exc).__name__ == "BlobNotFoundError"

_BLOB_PREFIX = "mafhum/"
_KEY_PATTERN = re.compile(r"^[0-9a-f]{2}/[0-9a-f]{64}$")
_SPOOL_MEMORY_BYTES = 1024 * 1024


class _BlobDownload(Protocol):
    def __enter__(self) -> Iterator[bytes]: ...

    def __exit__(self, exc_type: object, exc: object, tb: object) -> object: ...


class _BlobItem(Protocol):
    pathname: str
    size: int


class _BlobMetadata(Protocol):
    pathname: str
    size: int


class _BlobClient(Protocol):
    def put_blob(
        self,
        pathname: str,
        body: object,
        *,
        access: str,
        content_length: int,
        allow_overwrite: bool,
    ) -> object: ...

    def stream_blob(self, pathname: str, *, access: str) -> _BlobDownload: ...

    def head_blob(self, pathname: str) -> _BlobMetadata: ...

    def delete_blob(self, pathname: str) -> None: ...

    def query_blobs(self, *, prefix: str) -> Iterable[_BlobItem]: ...


class VercelBlobStorage:
    """Private Vercel Blob adapter; no public URLs are accepted or persisted."""

    def __init__(self, client: _BlobClient | None = None) -> None:
        if client is None:
            try:
                from vercel.blob import sync as blob
            except ImportError as exc:
                raise StorageError("Vercel Blob SDK is not installed") from exc
            client = cast(_BlobClient, blob)
        self._client = client

    def generate_key(self) -> str:
        from mafhum_api.services.documents.storage import new_storage_key

        return new_storage_key()

    @staticmethod
    def _pathname(key: str) -> str:
        if not isinstance(key, str) or not _KEY_PATTERN.fullmatch(key):
            raise StorageKeyError("invalid storage key")
        return f"{_BLOB_PREFIX}{key}"

    @staticmethod
    def _key_from_pathname(pathname: str) -> str | None:
        if not pathname.startswith(_BLOB_PREFIX):
            return None
        key = pathname[len(_BLOB_PREFIX) :]
        return key if _KEY_PATTERN.fullmatch(key) else None

    def write(self, key: str, chunks: Iterable[bytes], *, max_bytes: int) -> StorageObject:
        """Write bounded chunks without buffering the complete object in RAM."""
        pathname = self._pathname(key)
        if max_bytes <= 0:
            raise StorageError("invalid upload size limit")

        size = 0
        try:
            with tempfile.SpooledTemporaryFile(
                max_size=min(_SPOOL_MEMORY_BYTES, max_bytes), mode="w+b"
            ) as spool:
                for chunk in chunks:
                    if not isinstance(chunk, bytes):
                        raise StorageError("upload stream yielded a non-bytes chunk")
                    if not chunk:
                        continue
                    size += len(chunk)
                    if size > max_bytes:
                        raise StorageError("upload exceeds maximum size")
                    spool.write(chunk)
                spool.seek(0)
                result = self._client.put_blob(
                    pathname,
                    spool,
                    access="private",
                    content_length=size,
                    allow_overwrite=False,
                )
                returned_pathname = getattr(result, "pathname", None)
                if returned_pathname != pathname:
                    # A random suffix or rewritten path would break the DB key
                    # contract; fail closed and remove only the returned object.
                    if isinstance(returned_pathname, str) and returned_pathname.startswith(
                        _BLOB_PREFIX
                    ):
                        with suppress(Exception):
                            self._client.delete_blob(returned_pathname)
                    raise StorageError("Blob provider returned an unexpected pathname")
        except StorageError:
            raise
        except Exception as exc:
            raise StorageError("could not write private Blob object") from exc

        return StorageObject(key=key, size_bytes=size)

    def open_read(self, key: str) -> Iterator[bytes]:
        pathname = self._pathname(key)

        def _stream() -> Iterator[bytes]:
            try:
                with self._client.stream_blob(pathname, access="private") as download:
                    yield from download
            except Exception as exc:
                if _is_blob_not_found(exc):
                    raise StorageObjectNotFoundError("object not found") from exc
                if isinstance(exc, StorageError):
                    raise
                raise StorageError("could not read private Blob object") from exc

        return _stream()

    def exists(self, key: str) -> bool:
        try:
            self.stat(key)
            return True
        except StorageObjectNotFoundError:
            return False

    def stat(self, key: str) -> StorageObject:
        pathname = self._pathname(key)
        try:
            metadata = self._client.head_blob(pathname)
        except Exception as exc:
            if _is_blob_not_found(exc):
                raise StorageObjectNotFoundError("object not found") from exc
            raise StorageError("could not inspect private Blob object") from exc
        if metadata.pathname != pathname or not isinstance(metadata.size, int) or metadata.size < 0:
            raise StorageError("Blob provider returned invalid object metadata")
        return StorageObject(key=key, size_bytes=metadata.size)

    def delete(self, key: str) -> bool:
        pathname = self._pathname(key)
        try:
            self._client.head_blob(pathname)
        except Exception as exc:
            if _is_blob_not_found(exc):
                return False
            raise StorageError("could not verify private Blob object before deletion") from exc
        try:
            self._client.delete_blob(pathname)
        except Exception as exc:
            raise StorageError("could not delete private Blob object") from exc
        return True

    def delete_many(self, keys: Sequence[str]) -> tuple[list[str], list[str]]:
        deleted: list[str] = []
        failed: list[str] = []
        for key in keys:
            try:
                if self.delete(key):
                    deleted.append(key)
            except StorageError:
                failed.append(key)
                logger.error(
                    "private Blob deletion failed; key retained for reconciliation",
                    extra={"event": "storage_delete_failed"},
                )
        return deleted, failed

    def reconcile_orphans(self, live_keys: Sequence[str]) -> list[str]:
        """Report orphan keys only; this method never deletes objects."""
        live = set(live_keys)
        orphans: list[str] = []
        try:
            for item in self._client.query_blobs(prefix=_BLOB_PREFIX):
                key = self._key_from_pathname(item.pathname)
                if key is not None and key not in live:
                    orphans.append(key)
        except Exception as exc:
            raise StorageError("could not reconcile private Blob objects") from exc
        return orphans
