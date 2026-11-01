from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import BinaryIO

import pytest

from mafhum_api.services.documents.storage import (
    StorageError,
    StorageKeyError,
    StorageObjectNotFoundError,
)
from mafhum_api.services.documents.vercel_blob_storage import VercelBlobStorage


@dataclass
class _Metadata:
    pathname: str
    size: int


class _FakeBlobClient:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.put_access: list[str] = []
        self.read_access: list[str] = []
        self.put_lengths: list[int] = []
        self.put_body_was_stream: list[bool] = []
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
        self.put_access.append(access)
        self.put_lengths.append(content_length)
        self.put_body_was_stream.append(hasattr(body, "read"))
        if pathname in self.objects and not allow_overwrite:
            raise RuntimeError("already exists")
        data = body.read()
        if len(data) != content_length:
            raise RuntimeError("content length mismatch")
        self.objects[pathname] = data
        return type("PutResult", (), {"pathname": pathname})()

    @contextmanager
    def stream_blob(self, pathname: str, *, access: str) -> Iterator[Iterator[bytes]]:
        self.read_access.append(access)
        if pathname not in self.objects:
            raise FileNotFoundError(pathname)
        value = self.objects[pathname]
        yield iter(value[i : i + 3] for i in range(0, len(value), 3))

    def head_blob(self, pathname: str) -> _Metadata:
        if pathname not in self.objects:
            raise FileNotFoundError(pathname)
        return _Metadata(pathname, len(self.objects[pathname]))

    def delete_blob(self, pathname: str) -> None:
        self.delete_calls.append(pathname)
        self.objects.pop(pathname, None)

    def query_blobs(self, *, prefix: str) -> Iterator[_Metadata]:
        for pathname, value in self.objects.items():
            if pathname.startswith(prefix):
                yield _Metadata(pathname, len(value))


def test_upload_is_private_streamed_and_non_overwriting() -> None:
    client = _FakeBlobClient()
    storage = VercelBlobStorage(client)
    key = "ab/" + "1" * 64

    stored = storage.write(key, [b"abc", b"", b"def"], max_bytes=6)

    assert stored.key == key
    assert stored.size_bytes == 6
    assert client.objects["mafhum/" + key] == b"abcdef"
    assert client.put_access == ["private"]
    assert client.put_lengths == [6]
    assert client.put_body_was_stream == [True]
    with pytest.raises(StorageError):
        storage.write(key, [b"replacement"], max_bytes=20)
    assert client.objects["mafhum/" + key] == b"abcdef"


def test_upload_rejects_over_limit_before_provider_write() -> None:
    client = _FakeBlobClient()
    storage = VercelBlobStorage(client)

    with pytest.raises(StorageError):
        storage.write("ab/" + "2" * 64, [b"123", b"456"], max_bytes=5)

    assert client.objects == {}
    assert client.put_access == []


def test_invalid_keys_are_rejected_before_provider_calls() -> None:
    client = _FakeBlobClient()
    storage = VercelBlobStorage(client)

    for key in ("../secret", "ab/../" + "0" * 64, "AB/" + "0" * 64, "ab/" + "0" * 63):
        with pytest.raises(StorageKeyError):
            storage.stat(key)

    assert client.objects == {}


def test_private_stream_and_metadata() -> None:
    client = _FakeBlobClient()
    storage = VercelBlobStorage(client)
    key = "cd/" + "3" * 64
    storage.write(key, [b"hello world"], max_bytes=100)

    assert b"".join(storage.open_read(key)) == b"hello world"
    assert client.read_access == ["private"]
    assert storage.stat(key).size_bytes == 11
    assert storage.exists(key)


def test_missing_objects_are_reported_as_not_found() -> None:
    storage = VercelBlobStorage(_FakeBlobClient())

    with pytest.raises(StorageObjectNotFoundError):
        storage.stat("ef/" + "4" * 64)
    with pytest.raises(StorageObjectNotFoundError):
        list(storage.open_read("ef/" + "4" * 64))


def test_delete_is_idempotent_and_orphan_reconciliation_is_report_only() -> None:
    client = _FakeBlobClient()
    storage = VercelBlobStorage(client)
    live = "aa/" + "5" * 64
    orphan = "bb/" + "6" * 64
    storage.write(live, [b"live"], max_bytes=20)
    storage.write(orphan, [b"orphan"], max_bytes=20)

    assert storage.delete(live) is True
    assert storage.delete(live) is False
    assert storage.reconcile_orphans([live]) == [orphan]
    assert "mafhum/" + orphan in client.objects
    assert client.delete_calls == ["mafhum/" + live]
