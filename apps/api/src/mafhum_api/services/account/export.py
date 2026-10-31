"""Bounded, ownership-isolated export of one account's data.

Threat model
------------
`GET /api/v1/me/export` is the highest-bandwidth read in the application: it
turns one HTTP request into the concatenation of every private blob a user owns.
The rules here exist so it cannot become a cross-user read, a memory-exhaustion
primitive, a path-traversal vector in the archive or a never-ending request.

* Every database query carries ``owner_id == user.id`` in SQL. No object is
  looked up by an identifier supplied by the caller.
* Archive member names are built ONLY from a row's UUID and a whitelisted
  extension. A user-controlled ``display_filename`` never reaches ``ZipFile``;
  it appears as JSON text in the manifest instead.
* Files are copied in fixed-size chunks into a ``SpooledTemporaryFile`` with a
  hard total/per-file/file-count cap and a wall-clock deadline. Only
  ``spool_memory_bytes`` is ever resident in RAM; the rest is a private temp
  file (mode 0600), and the response streams it back in chunks.
* A missing blob or an exceeded cap is reported in the manifest and in the
  ``truncated`` flag. The export is never silently incomplete.

What is NOT exported: password hashes, session tokens, CSRF secrets, login
attempts, other users' rows, audit events about other actors, or extracted text
segments (they are a derived copy of the original files that are included).
"""

from __future__ import annotations

import json
import logging
import tempfile
import time
import uuid
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from pathlib import PurePosixPath
from typing import IO, Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from mafhum_api.db.models import (
    Document,
    PresentationArtifact,
    PresentationJob,
    Project,
    SourceRecord,
    StudyTopic,
    User,
)
from mafhum_api.services.documents.storage import (
    DocumentStorage,
    StorageError,
    StorageObjectNotFoundError,
)

logger = logging.getLogger(__name__)

_CHUNK = 64 * 1024
_ZIP_MEDIA_TYPE = "application/zip"
_ALLOWED_EXTENSIONS: frozenset[str] = frozenset({"pdf", "docx", "pptx"})
_SCHEMA_VERSION = 1

README = """MAFHUM — account data export / تصدير بيانات الحساب

This archive contains the data MAFHUM holds about your account. The JSON files
are machine-readable metadata; `files/` holds your original uploaded documents
and any presentations you generated.

Names inside `files/` are UUIDs, not your original filenames: `manifest.json`
maps each archive member back to the name you used when uploading. Original
filenames may contain characters that are unsafe in a ZIP path.

Passwords, session tokens, login attempts and other users' data are never
included. Extracted text is omitted because the original documents are here.

If `truncated` is true in manifest.json, one of the configured export limits was
reached. The `notices` array says which one. Request a new export after removing
some documents, or ask an operator to raise the limit deliberately.

تحتوي هذه الأرشيفة على بيانات حسابك في مَفْهُوم. ملفات JSON هي بيانات وصفية،
ومجلد files يحتوي على مستنداتك الأصلية والعروض التي أنشأتها. لا تُضمَّن كلمات
المرور أو رموز الجلسات أو بيانات المستخدمين الآخرين.
"""


@dataclass(frozen=True, slots=True)
class ExportLimits:
    max_files: int
    max_file_bytes: int
    max_total_bytes: int
    spool_memory_bytes: int
    max_seconds: float


@dataclass(slots=True)
class ExportResult:
    """A finished archive, ready to stream. Caller must close ``stream``."""

    stream: IO[bytes]
    size_bytes: int
    file_count: int
    metadata_rows: int
    truncated: bool
    notices: list[str] = field(default_factory=list)

    @property
    def media_type(self) -> str:
        return _ZIP_MEDIA_TYPE


def _jsonable(value: Any) -> Any:
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _rows(
    db: Session, model: Any, owner_id: uuid.UUID, columns: tuple[str, ...], cap: int
) -> tuple[list[dict[str, Any]], bool]:
    """Owner-scoped, bounded metadata read. Returns (rows, truncated)."""
    stmt = (
        select(model)
        .where(model.owner_id == owner_id)
        .order_by(model.created_at.asc())
        .limit(cap + 1)
    )
    fetched = list(db.scalars(stmt).all())
    truncated = len(fetched) > cap
    records = [
        {column: _jsonable(getattr(row, column, None)) for column in columns}
        for row in fetched[:cap]
    ]
    return records, truncated


def _safe_member_name(row_id: Any, raw_format: Any) -> str:
    """Build an archive path that cannot escape, collide or carry user text."""
    identifier = uuid.uuid5(uuid.NAMESPACE_URL, f"mafhum-export:{row_id}").hex
    extension = str(raw_format or "").lower()
    if extension not in _ALLOWED_EXTENSIONS:
        extension = "bin"
    name = PurePosixPath("files") / f"{identifier}.{extension}"
    return name.as_posix()


class AccountExportService:
    """Build one account's export archive. Instantiated per request is fine."""

    def __init__(self, storage: DocumentStorage, limits: ExportLimits) -> None:
        self._storage = storage
        self._limits = limits

    def build(self, db: Session, user: User) -> ExportResult:
        owner_id = user.id
        deadline = time.monotonic() + self._limits.max_seconds
        notices: list[str] = []
        spool: IO[bytes] = tempfile.SpooledTemporaryFile(  # noqa: SIM115 - closed by the streaming generator
            max_size=self._limits.spool_memory_bytes, mode="w+b"
        )
        metadata_cap = max(1, self._limits.max_files * 2)

        try:
            with zipfile.ZipFile(spool, mode="w", compression=zipfile.ZIP_STORED) as archive:
                archive.writestr("README.txt", README.encode("utf-8"))

                # ---- metadata: every query is owner-scoped -------------------
                account = {
                    "id": _jsonable(user.id),
                    "email": _jsonable(user.email),
                    "display_name": _jsonable(user.display_name),
                    "preferred_language": _jsonable(user.preferred_language),
                    "role": _jsonable(user.role),
                    "created_at": _jsonable(user.created_at),
                }
                archive.writestr("account.json", _dump(account))

                projects, projects_truncated = _rows(
                    db,
                    Project,
                    owner_id,
                    ("id", "title", "description", "language", "created_at", "updated_at"),
                    metadata_cap,
                )
                documents, documents_truncated = _rows(
                    db,
                    Document,
                    owner_id,
                    (
                        "id",
                        "project_id",
                        "display_filename",
                        "format",
                        "size_bytes",
                        "status",
                        "page_count",
                        "char_count",
                        "segment_count",
                        "language_hint",
                        "scan_verdict",
                        "parser_name",
                        "created_at",
                        "processed_at",
                    ),
                    metadata_cap,
                )
                sources, sources_truncated = _rows(
                    db,
                    SourceRecord,
                    owner_id,
                    (
                        "id",
                        "document_id",
                        "title",
                        "authors",
                        "institution",
                        "publisher",
                        "publication_year",
                        "source_type",
                        "doi",
                        "isbn",
                        "url",
                        "language",
                        "page_count",
                        "provenance",
                        "verification_status",
                        "notes",
                        "created_at",
                    ),
                    metadata_cap,
                )
                presentations, presentations_truncated = _rows(
                    db,
                    PresentationJob,
                    owner_id,
                    (
                        "id",
                        "title",
                        "question",
                        "language",
                        "status",
                        "slide_count",
                        "citation_count",
                        "generator_name",
                        "generator_version",
                        "error_code",
                        "created_at",
                        "finished_at",
                    ),
                    metadata_cap,
                )
                topics, topics_truncated = _rows(
                    db,
                    StudyTopic,
                    owner_id,
                    ("id", "title", "description", "language", "archived", "created_at"),
                    metadata_cap,
                )
                for name, rows in (
                    ("projects.json", projects),
                    ("documents.json", documents),
                    ("sources.json", sources),
                    ("presentations.json", presentations),
                    ("study_topics.json", topics),
                ):
                    archive.writestr(name, _dump(rows))
                if any(
                    (
                        projects_truncated,
                        documents_truncated,
                        sources_truncated,
                        presentations_truncated,
                        topics_truncated,
                    )
                ):
                    notices.append("metadata_row_limit")

                # ---- original blobs --------------------------------------------
                # Keys come from owner-scoped rows only, so no caller-controlled
                # storage key can ever be resolved here.
                artifact_rows = list(
                    db.scalars(
                        select(PresentationArtifact)
                        .where(PresentationArtifact.owner_id == owner_id)
                        .order_by(PresentationArtifact.created_at.asc())
                        .limit(metadata_cap)
                    ).all()
                )
                candidates: list[tuple[str, str, str, str]] = []
                for document in db.scalars(
                    select(Document)
                    .where(Document.owner_id == owner_id)
                    .order_by(Document.created_at.asc())
                    .limit(metadata_cap)
                ).all():
                    candidates.append(
                        (
                            str(document.id),
                            str(document.storage_key),
                            str(document.display_filename),
                            str(document.format or ""),
                        )
                    )
                for artifact in artifact_rows:
                    candidates.append(
                        (
                            str(artifact.id),
                            str(artifact.storage_key),
                            str(artifact.display_filename),
                            str(artifact.format or "pptx"),
                        )
                    )

                manifest_files: list[dict[str, Any]] = []
                missing: list[str] = []
                total_bytes = 0
                file_count = 0
                stop_reason = ""
                for row_id, key, original_name, file_format in candidates:
                    if time.monotonic() >= deadline:
                        stop_reason = "export_time_limit"
                        break
                    if file_count >= self._limits.max_files:
                        stop_reason = "export_file_count_limit"
                        break
                    # `stat` is the source of truth for size, not the DB column:
                    # a stale row must not let a huge blob into the archive.
                    try:
                        actual_size = int(self._storage.stat(key).size_bytes)
                    except (StorageObjectNotFoundError, StorageError):
                        missing.append(row_id)
                        continue
                    if actual_size > self._limits.max_file_bytes:
                        notices.append("file_size_limit")
                        continue
                    if total_bytes + actual_size > self._limits.max_total_bytes:
                        stop_reason = "export_total_bytes_limit"
                        break

                    member = _safe_member_name(row_id, file_format)
                    try:
                        chunks = self._storage.open_read(key)
                    except (StorageObjectNotFoundError, StorageError):
                        missing.append(row_id)
                        continue

                    # The current member is always written to completion: a
                    # half-written ZIP entry is worse than a slightly late stop.
                    # The deadline is honoured at the next file boundary.
                    written = 0
                    try:
                        with archive.open(member, "w") as target:
                            for chunk in chunks:
                                target.write(chunk)
                                written += len(chunk)
                    finally:
                        close = getattr(chunks, "close", None)
                        if close is not None:
                            close()

                    total_bytes += written
                    file_count += 1
                    manifest_files.append(
                        {
                            "archive_name": member,
                            "original_filename": original_name,
                            "size_bytes": written,
                            "declared_size_bytes": actual_size,
                            "row_id": row_id,
                        }
                    )
                if stop_reason:
                    notices.append(stop_reason)

                manifest = {
                    "schema_version": _SCHEMA_VERSION,
                    "generated_at": datetime.now(UTC).isoformat(),
                    "account_id": str(owner_id),
                    "truncated": bool(notices),
                    "notices": notices,
                    "missing_blobs": missing,
                    "files": manifest_files,
                    "counts": {
                        "files": file_count,
                        "blob_bytes": total_bytes,
                        "projects": len(projects),
                        "documents": len(documents),
                        "sources": len(sources),
                        "presentations": len(presentations),
                        "study_topics": len(topics),
                    },
                    "limits": {
                        "max_files": self._limits.max_files,
                        "max_file_bytes": self._limits.max_file_bytes,
                        "max_total_bytes": self._limits.max_total_bytes,
                        "max_seconds": self._limits.max_seconds,
                    },
                    "excluded_by_design": [
                        "password_hash",
                        "sessions",
                        "csrf_tokens",
                        "login_attempts",
                        "other_users_rows",
                        "audit_events",
                        "extracted_text_segments",
                    ],
                }
                archive.writestr("manifest.json", _dump(manifest))

            spool.seek(0)
            size = spool.seek(0, 2)
            spool.seek(0)
            return ExportResult(
                stream=spool,
                size_bytes=int(size),
                file_count=len(manifest_files),
                metadata_rows=sum(
                    len(item) for item in (projects, documents, sources, presentations, topics)
                ),
                truncated=bool(notices),
                notices=notices,
            )
        except Exception:
            spool.close()
            raise


def _dump(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")


def stream_chunks(result: ExportResult, chunk_size: int = _CHUNK) -> Iterator[bytes]:
    """Yield the archive in bounded pieces and always close the spool."""
    try:
        while True:
            chunk = result.stream.read(chunk_size)
            if not chunk:
                break
            yield chunk
    finally:
        result.stream.close()
