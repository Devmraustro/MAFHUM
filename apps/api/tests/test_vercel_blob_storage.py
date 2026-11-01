from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import BinaryIO

import pytest
from vercel.blob.errors import BlobNotFoundError

from mafhum_api.services.documents.storage import (
    StorageError,
    StorageKeyError,
    StorageObjectNotFoundError,
)
from mafhum_api.services.documents.vercel_blob_storage import (
    VercelBlobStorage,
    _parse_read_write_token,
)


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
            # The pinned SDK's real not-found error: BlobNotFoundError is a
            # FileNotFoundError, which is exactly what the adapter maps.
            raise BlobNotFoundError(pathname)
        value = self.objects[pathname]
        yield iter(value[i : i + 3] for i in range(0, len(value), 3))

    def head_blob(self, pathname: str) -> _Metadata:
        if pathname not in self.objects:
            raise BlobNotFoundError(pathname)
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


# ------------------------------------------------------- credential wiring
def test_read_write_token_is_parsed_and_stays_out_of_errors() -> None:
    store_id = _parse_read_write_token("vercel_blob_rw_store123_secret456")

    assert store_id == "store123"
    for bad in ("", "not-a-token", "vercel_blob_rw_", "vercel_blob_rw_onlystore"):
        with pytest.raises(StorageError, match="invalid format"):
            _parse_read_write_token(bad)


def test_explicit_token_builds_sdk_session_credentials() -> None:
    storage = VercelBlobStorage(
        _FakeBlobClient(), blob_read_write_token="vercel_blob_rw_store123_secret456"
    )

    assert storage._session_options is not None
    options = storage._session_options
    credentials_factory = options.credentials_factory  # type: ignore[attr-defined]
    credentials = credentials_factory()
    assert credentials.token == "vercel_blob_rw_store123_secret456"
    assert credentials.store_id == "store123"
    assert credentials.kind.value == "read_write"


def test_malformed_token_fails_at_construction_not_first_request() -> None:
    with pytest.raises(StorageError, match="invalid format"):
        VercelBlobStorage(_FakeBlobClient(), blob_read_write_token="oops")


def test_real_client_without_credentials_fails_fast() -> None:
    # No injected fake client and no credentials: the SDK's default resolution
    # has nothing to read (BLOB_READ_WRITE_TOKEN / BLOB_STORE_ID absent), so
    # construction must refuse instead of failing on the first request.
    with pytest.raises(StorageError, match="requires credentials"):
        VercelBlobStorage()


def test_store_id_only_keeps_sdk_default_resolution() -> None:
    # OIDC mode: no explicit session binding, but construction succeeds.
    storage = VercelBlobStorage(_FakeBlobClient(), blob_store_id="store123")

    assert storage._session_options is None


def test_operations_bind_the_credential_session() -> None:
    client = _FakeBlobClient()
    storage = VercelBlobStorage(client, blob_read_write_token="vercel_blob_rw_store123_secret456")
    key = "ab/" + "7" * 64
    bound: list[bool] = []
    real_bind = storage._bind_session

    def _counting_bind() -> object:
        bound.append(True)
        return real_bind()

    storage._bind_session = _counting_bind  # type: ignore[method-assign]
    storage.write(key, [b"x"], max_bytes=10)
    storage.stat(key)
    list(storage.open_read(key))
    storage.delete(key)

    # write (put + no compensating delete), stat, read, delete-verify,
    # delete-act: every SDK call ran inside the bound session.
    assert len(bound) >= 5


# ------------------------------------------------------- settings integration
def test_production_requires_blob_credentials_when_backend_selected() -> None:
    from mafhum_api.config import Settings

    base = {
        "app_env": "production",
        "session_secret": "x" * 48,
        "cookie_secure": True,
        "database_url": "postgresql+psycopg://u:p@db.example.com:5432/mafhum",
        "malware_scan_required": True,
        "clamd_host": "clamd.internal",
        "metrics_scrape_token": "m" * 40,
        "storage_backend": "vercel_blob",
    }

    with pytest.raises(RuntimeError, match="BLOB_READ_WRITE_TOKEN or BLOB_STORE_ID"):
        Settings(**base).assert_production_ready()
    Settings(**base, blob_read_write_token="vercel_blob_rw_s_x").assert_production_ready()
    Settings(**base, blob_store_id="store123").assert_production_ready()


def test_local_backend_never_requires_blob_credentials() -> None:
    from mafhum_api.config import Settings

    # The default (local) backend keeps booting with no Blob configuration.
    Settings(app_env="development").assert_production_ready()


def test_build_context_wires_blob_credentials_from_settings(tmp_path) -> None:
    from mafhum_api.config import Settings
    from mafhum_api.main import build_context
    from mafhum_api.services.documents.vercel_blob_storage import VercelBlobStorage

    settings = Settings(
        storage_backend="vercel_blob",
        blob_read_write_token="vercel_blob_rw_store123_secret456",
        storage_root=tmp_path,
        db_pool_pre_ping=True,
    )
    context = build_context(settings)
    assert isinstance(context.storage, VercelBlobStorage)
    assert context.storage._session_options is not None

    settings = Settings(storage_backend="vercel_blob", storage_root=tmp_path)
    with pytest.raises(StorageError, match="requires credentials"):
        build_context(settings)


# ------------------------------------------------------- provider failure paths
class _FlakyBlobClient(_FakeBlobClient):
    """Fake that can be told to fail individual operations."""

    def __init__(self) -> None:
        super().__init__()
        self.fail_put: Exception | None = None
        self.fail_head: Exception | None = None
        self.fail_stream: Exception | None = None
        self.fail_query: Exception | None = None

    def put_blob(self, pathname: str, body: BinaryIO, **kwargs: object) -> object:
        if self.fail_put is not None:
            raise self.fail_put
        return super().put_blob(pathname, body, **kwargs)  # type: ignore[arg-type]

    def head_blob(self, pathname: str) -> _Metadata:
        if self.fail_head is not None:
            raise self.fail_head
        return super().head_blob(pathname)

    @contextmanager
    def stream_blob(self, pathname: str, *, access: str) -> Iterator[Iterator[bytes]]:
        if self.fail_stream is not None:
            raise self.fail_stream
        with super().stream_blob(pathname, access=access) as download:
            yield download

    def query_blobs(self, *, prefix: str) -> Iterator[_Metadata]:
        if self.fail_query is not None:
            raise self.fail_query
        yield from super().query_blobs(prefix=prefix)


def test_transient_provider_failures_map_to_storage_error() -> None:
    from vercel.blob.errors import BlobServiceNotAvailable, BlobServiceRateLimited

    client = _FlakyBlobClient()
    storage = VercelBlobStorage(client)
    key = "ab/" + "8" * 64

    for failure in (
        TimeoutError("provider timeout"),
        BlobServiceNotAvailable(),
        BlobServiceRateLimited(),
        RuntimeError("connection reset"),
    ):
        client.fail_put = failure
        with pytest.raises(StorageError):
            storage.write(key, [b"x"], max_bytes=10)
        client.fail_head = failure
        with pytest.raises(StorageError):
            storage.stat(key)
        client.fail_stream = failure
        with pytest.raises(StorageError):
            list(storage.open_read(key))
        client.fail_query = failure
        with pytest.raises(StorageError):
            storage.reconcile_orphans([])

    client.fail_put = client.fail_head = client.fail_stream = client.fail_query = None
    assert client.objects == {}  # nothing was ever published


def test_content_length_mismatch_is_refused() -> None:
    from vercel.blob.errors import BlobContentLengthError

    client = _FlakyBlobClient()
    storage = VercelBlobStorage(client)
    client.fail_put = BlobContentLengthError("declared 5 bytes but received 4")

    # The provider reports the mismatch (the SDK's real error for this case);
    # the adapter maps it to StorageError and never publishes the object.
    with pytest.raises(StorageError):
        storage.write("ab/" + "9" * 64, [b"12345"], max_bytes=5)
    assert client.objects == {}


def test_malformed_provider_metadata_is_rejected() -> None:
    client = _FakeBlobClient()
    storage = VercelBlobStorage(client)
    key = "cd/" + "1" * 64
    storage.write(key, [b"payload"], max_bytes=100)

    for bad in (
        _Metadata("mafhum/someone-elses-key", 7),  # pathname mismatch
        _Metadata("mafhum/" + key, -1),  # negative size
        _Metadata("mafhum/" + key, "seven"),  # non-int size
    ):
        client.head_blob = lambda pathname, _bad=bad: _bad  # type: ignore[assignment]
        with pytest.raises(StorageError):
            storage.stat(key)


def test_delete_many_reports_partial_failure_and_keeps_rest() -> None:
    client = _FlakyBlobClient()
    storage = VercelBlobStorage(client)
    keys = ["ab/" + "2" * 64, "cd/" + "3" * 64, "ef/" + "4" * 64]
    for key in keys:
        storage.write(key, [b"x"], max_bytes=10)

    original_head = client.head_blob
    state = {"calls": 0}

    def _failing_head(pathname: str) -> _Metadata:
        state["calls"] += 1
        if pathname.endswith(keys[1][3:]):
            raise RuntimeError("provider outage mid-batch")
        return original_head(pathname)

    client.head_blob = _failing_head  # type: ignore[assignment]
    deleted, failed = storage.delete_many(keys)

    assert deleted == [keys[0], keys[2]]
    assert failed == [keys[1]]
    # The failed object is still there, awaiting reconciliation.
    assert "mafhum/" + keys[1] in client.objects
