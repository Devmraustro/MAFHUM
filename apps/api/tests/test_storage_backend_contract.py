"""Shared behavioral contract for every DocumentStorage backend.

The same storage contract that the document, deletion, export and
presentation services rely on is executed against each backend: the local
filesystem adapter (production default and development) and the private
Vercel Blob adapter (backed by a faithful fake of the pinned SDK surface).
A backend that satisfies only its own tests is not enough — they must be
interchangeable wherever the protocol is used.

Documented, deliberate differences (tested in backend-specific suites):
- Key strictness: the Blob adapter additionally enforces its strict
  ``<shard>/<opaque>`` pathname format; local accepts any safe relative key.
- Chunk types: callers always pass ``bytes`` (FastAPI upload streams); the
  Blob adapter still defends against non-bytes chunks explicitly.
- Failure wrapping: local propagates original exceptions after cleanup; the
  Blob adapter wraps provider failures in ``StorageError``.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import BinaryIO

import pytest
from vercel.blob.errors import BlobNotFoundError

from mafhum_api.services.documents.storage import (
    LocalFileStorage,
    StorageError,
    StorageKeyError,
    StorageObjectNotFoundError,
)
from mafhum_api.services.documents.vercel_blob_storage import VercelBlobStorage


@dataclass
class _Metadata:
    pathname: str
    size: int


class FakeBlobClient:
    """Faithful fake of the pinned SDK's synchronous surface."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.delete_calls: list[str] = []

    def put_blob(
        self,
        pathname: str,
        body: BinaryIO,
        *,
        access: str,
        content_length: int,
        allow_overwrite: bool,
    ) -> object:
        assert access == "private"
        assert allow_overwrite is False
        if pathname in self.objects:
            raise FileExistsError(pathname)
        data = body.read()
        if len(data) != content_length:
            raise StorageError("content length mismatch")
        self.objects[pathname] = data
        return type("PutResult", (), {"pathname": pathname})()

    @contextmanager
    def stream_blob(self, pathname: str, *, access: str) -> Iterator[Iterator[bytes]]:
        assert access == "private"
        if pathname not in self.objects:
            raise BlobNotFoundError(pathname)
        value = self.objects[pathname]
        yield iter(value[i : i + 4096] for i in range(0, len(value), 4096))

    def head_blob(self, pathname: str) -> _Metadata:
        if pathname not in self.objects:
            raise BlobNotFoundError(pathname)
        return _Metadata(pathname, len(self.objects[pathname]))

    def delete_blob(self, pathname: str) -> None:
        self.delete_calls.append(pathname)
        self.objects.pop(pathname, None)

    def query_blobs(self, *, prefix: str) -> Iterator[_Metadata]:
        for pathname, value in sorted(self.objects.items()):
            if pathname.startswith(prefix):
                yield _Metadata(pathname, len(value))


def valid_key(storage: object) -> str:
    return storage.generate_key()  # type: ignore[attr-defined]


@pytest.fixture(params=["local", "vercel_blob"])
def backend(request: pytest.FixtureRequest, tmp_path: object) -> object:
    if request.param == "local":
        return LocalFileStorage(tmp_path)  # type: ignore[arg-type]
    return VercelBlobStorage(FakeBlobClient())


class TestStorageContract:
    def test_generated_keys_are_opaque_and_sharded(self, backend: object) -> None:
        keys = {valid_key(backend) for _ in range(64)}
        assert len(keys) == 64
        for key in keys:
            assert "/" in key
            assert len(key.split("/")[-1]) >= 32

    def test_write_then_read_round_trip(self, backend: object) -> None:
        key = valid_key(backend)
        payload = b"m" * 100_000  # crosses the Blob adapter's spool threshold

        stored = backend.write(key, [payload[:4096], b"", payload[4096:]], max_bytes=len(payload))

        assert stored.key == key
        assert stored.size_bytes == len(payload)
        assert backend.stat(key).size_bytes == len(payload)
        assert backend.exists(key) is True
        joined = b"".join(backend.open_read(key))
        assert joined == payload

    def test_empty_uploads_are_preserved(self, backend: object) -> None:
        key = valid_key(backend)

        stored = backend.write(key, [], max_bytes=10)

        assert stored.size_bytes == 0
        assert backend.exists(key) is True
        assert b"".join(backend.open_read(key)) == b""

    def test_over_limit_writes_are_refused_whole(self, backend: object) -> None:
        key = valid_key(backend)

        with pytest.raises(StorageError):
            backend.write(key, [b"a" * 11], max_bytes=10)

        assert backend.exists(key) is False

    def test_chunk_stream_failure_leaves_no_object(self, backend: object) -> None:
        key = valid_key(backend)

        def _exploding() -> Iterator[bytes]:
            yield b"partial"
            raise RuntimeError("client disconnected mid-upload")

        # Either backend surfaces the failure (original or wrapped); the
        # invariant is that no partial object survives it.
        with pytest.raises((StorageError, RuntimeError)):
            backend.write(key, _exploding(), max_bytes=100)

        assert backend.exists(key) is False

    def test_writes_never_overwrite(self, backend: object) -> None:
        key = valid_key(backend)
        backend.write(key, [b"original"], max_bytes=100)

        with pytest.raises(StorageError):
            backend.write(key, [b"replacement"], max_bytes=100)

        assert b"".join(backend.open_read(key)) == b"original"

    def test_invalid_keys_are_refused_before_any_backend_call(self, backend: object) -> None:
        for bad in ("", "../escape", "a/../../b", "ab\\cd", "x\x00y"):
            with pytest.raises(StorageKeyError):
                backend.stat(bad)
            with pytest.raises(StorageKeyError):
                backend.write(bad, [b"x"], max_bytes=10)
            # Deletion fail-closes on both backends; local records the refusal
            # and returns False, the Blob adapter raises. Neither deletes.
            try:
                result = backend.delete(bad)
            except StorageKeyError:
                result = False
            assert result is False

    def test_missing_objects_map_to_not_found(self, backend: object) -> None:
        key = valid_key(backend)
        assert backend.exists(key) is False
        with pytest.raises(StorageObjectNotFoundError):
            backend.stat(key)
        with pytest.raises(StorageObjectNotFoundError):
            list(backend.open_read(key))

    def test_delete_is_idempotent(self, backend: object) -> None:
        key = valid_key(backend)
        backend.write(key, [b"x"], max_bytes=10)

        assert backend.delete(key) is True
        assert backend.delete(key) is False
        assert backend.exists(key) is False

    def test_delete_many_reports_failures_without_losing_succeeds(self, backend: object) -> None:
        keys = [valid_key(backend) for _ in range(3)]
        for key in keys:
            backend.write(key, [b"x"], max_bytes=10)

        deleted, failed = backend.delete_many(keys)

        assert sorted(deleted) == sorted(keys)
        assert failed == []

    def test_orphan_reconciliation_is_report_only(self, backend: object) -> None:
        live = valid_key(backend)
        orphan = valid_key(backend)
        backend.write(live, [b"live"], max_bytes=10)
        backend.write(orphan, [b"orphan"], max_bytes=10)

        reported = backend.reconcile_orphans([live])

        assert reported == [orphan]
        # Report-only: nothing was deleted by reconciliation itself.
        assert backend.exists(orphan) is True
        assert backend.exists(live) is True
