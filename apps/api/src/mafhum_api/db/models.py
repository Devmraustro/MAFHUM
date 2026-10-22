"""Persistent entities for identity, projects, documents and sources.

Design notes
------------
* Every user-owned row carries ``owner_id`` so ownership is a *column predicate*,
  never something inferred from a UUID being hard to guess.
* ``storage_key`` is an opaque, server-generated identifier. Absolute filesystem
  paths are never persisted.
* Enumerated columns carry DB-level CHECK constraints so invalid states cannot be
  written even by a buggy service or a hand-run migration.
* Size/page/counter columns are constrained non-negative at the database level.
"""

from __future__ import annotations

import enum
import uuid
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy import (
    Float as sa_Float,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON, TypeDecorator

from mafhum_api.db.base import Base


class JsonVariant(TypeDecorator[Any]):
    """JSONB on PostgreSQL, generic JSON elsewhere."""

    impl = JSON
    cache_ok = True

    def load_dialect_impl(self, dialect: Any) -> Any:
        if dialect.name == "postgresql":
            return dialect.type_descriptor(JSONB())
        return dialect.type_descriptor(JSON())


def utcnow() -> datetime:
    return datetime.now(UTC)


class DocumentFormat(str, enum.Enum):
    PDF = "pdf"
    DOCX = "docx"


class DocumentStatus(str, enum.Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    READY = "ready"
    FAILED = "failed"


class DeletionState(str, enum.Enum):
    ACTIVE = "active"
    PENDING_DELETION = "pending_deletion"
    DELETED = "deleted"


class JobStatus(str, enum.Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class SourceType(str, enum.Enum):
    USER_UPLOAD = "user_upload"
    LECTURE_NOTES = "lecture_notes"
    BOOK = "book"
    JOURNAL_ARTICLE = "journal_article"
    THESIS = "thesis"
    REPORT = "report"
    LEGAL_TEXT = "legal_text"
    WEBSITE = "website"
    OTHER = "other"


class ProvenanceLevel(str, enum.Enum):
    """Where a piece of metadata came from. Never collapse these levels."""

    USER_SUPPLIED = "user_supplied"
    DOCUMENT_EXTRACTED = "document_extracted"
    EXTERNAL_VERIFIED = "external_verified"


class VerificationStatus(str, enum.Enum):
    UNVERIFIED = "unverified"
    USER_ASSERTED = "user_asserted"
    VERIFIED = "verified"


# --- valid state transitions -------------------------------------------------
# The DB CHECK constrains the *set* of values; this table constrains *movement*
# between them and is enforced by ``DocumentService`` (see its tests).
DOCUMENT_TRANSITIONS: dict[DocumentStatus, frozenset[DocumentStatus]] = {
    DocumentStatus.PENDING: frozenset({DocumentStatus.PROCESSING, DocumentStatus.FAILED}),
    DocumentStatus.PROCESSING: frozenset(
        {DocumentStatus.READY, DocumentStatus.FAILED, DocumentStatus.PENDING}
    ),
    DocumentStatus.FAILED: frozenset({DocumentStatus.PENDING}),
    # ``ready`` is terminal for the processing state machine.
    DocumentStatus.READY: frozenset(),
}

JOB_TRANSITIONS: dict[JobStatus, frozenset[JobStatus]] = {
    JobStatus.QUEUED: frozenset({JobStatus.RUNNING, JobStatus.CANCELLED}),
    JobStatus.RUNNING: frozenset(
        {JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.QUEUED, JobStatus.CANCELLED}
    ),
    JobStatus.SUCCEEDED: frozenset(),
    JobStatus.FAILED: frozenset({JobStatus.QUEUED}),
    JobStatus.CANCELLED: frozenset(),
}


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class User(TimestampMixin, Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint("char_length(email) <= 320", name="ck_users_email_len"),
        CheckConstraint(
            "deleted_at IS NULL OR deletion_state <> 'active'", name="ck_users_deleted"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False, index=True)
    display_name: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    password_hash: Mapped[str] = mapped_column(String(256), nullable=False)
    preferred_language: Mapped[str] = mapped_column(String(8), nullable=False, default="ar")
    role: Mapped[str] = mapped_column(String(16), nullable=False, default="student")
    # Account deletion lifecycle.
    deletion_state: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=DeletionState.ACTIVE.value,
        server_default=DeletionState.ACTIVE.value,
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    sessions: Mapped[list[UserSession]] = relationship(
        cascade="all, delete-orphan", passive_deletes=True
    )
    projects: Mapped[list[Project]] = relationship(
        cascade="all, delete-orphan", passive_deletes=True
    )
    documents: Mapped[list[Document]] = relationship(
        cascade="all, delete-orphan", passive_deletes=True
    )
    sources: Mapped[list[SourceRecord]] = relationship(
        cascade="all, delete-orphan", passive_deletes=True
    )


class UserSession(TimestampMixin, Base):
    __tablename__ = "user_sessions"
    __table_args__ = (CheckConstraint("expires_at > created_at", name="ck_sessions_expiry"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(256), nullable=True)


class Project(TimestampMixin, Base):
    __tablename__ = "projects"
    __table_args__ = (
        CheckConstraint("char_length(title) BETWEEN 1 AND 200", name="ck_projects_title_len"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    language: Mapped[str] = mapped_column(String(8), nullable=False, default="ar")

    documents: Mapped[list[Document]] = relationship(back_populates="project")


class Document(TimestampMixin, Base):
    __tablename__ = "documents"
    __table_args__ = (
        UniqueConstraint("storage_key", name="uq_documents_storage_key"),
        CheckConstraint("size_bytes > 0", name="ck_documents_size_positive"),
        CheckConstraint("char_count >= 0", name="ck_documents_char_count_non_negative"),
        CheckConstraint("page_count IS NULL OR page_count > 0", name="ck_documents_page_count"),
        CheckConstraint(
            "status IN ('pending', 'processing', 'ready', 'failed')", name="ck_documents_status"
        ),
        CheckConstraint("format IN ('pdf', 'docx')", name="ck_documents_format"),
        CheckConstraint(
            "deletion_state IN ('active', 'pending_deletion', 'deleted')",
            name="ck_documents_deletion_state",
        ),
        CheckConstraint("segment_count >= 0", name="ck_documents_segment_count"),
        CheckConstraint(
            "error_message IS NULL OR char_length(error_message) <= 500",
            name="ck_documents_error_message_len",
        ),
        Index("ix_documents_owner_created", "owner_id", "created_at"),
        Index("ix_documents_owner_status", "owner_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("projects.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    # Server-generated opaque locator. Resolved to a path only by the storage
    # backend; never contains user input and never leaves the server as a path.
    storage_key: Mapped[str] = mapped_column(String(128), nullable=False)
    display_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    format: Mapped[str] = mapped_column(String(8), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default=DocumentStatus.PENDING.value,
        server_default=DocumentStatus.PENDING.value,
    )
    deletion_state: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=DeletionState.ACTIVE.value,
        server_default=DeletionState.ACTIVE.value,
    )
    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    char_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    segment_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    language_hint: Mapped[str | None] = mapped_column(String(8), nullable=True)
    is_scanned: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    parser_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    parser_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(48), nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(500), nullable=True)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    project: Mapped[Project | None] = relationship(back_populates="documents")
    jobs: Mapped[list[DocumentProcessingJob]] = relationship(
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="DocumentProcessingJob.generation",
    )
    segments: Mapped[list[DocumentTextSegment]] = relationship(
        cascade="all, delete-orphan", passive_deletes=True, order_by="DocumentTextSegment.ordinal"
    )
    sources: Mapped[list[SourceRecord]] = relationship(
        cascade="all, delete-orphan", passive_deletes=True
    )


class DocumentProcessingJob(TimestampMixin, Base):
    """One bounded processing attempt for a document.

    ``(document_id, generation)`` is unique, which is what makes retries
    idempotent: a retried run gets a new generation, and segments from older
    generations are removed in the same transaction that writes new ones.
    """

    __tablename__ = "document_processing_jobs"
    __table_args__ = (
        UniqueConstraint("document_id", "generation", name="uq_jobs_document_generation"),
        CheckConstraint("generation >= 1", name="ck_jobs_generation_positive"),
        CheckConstraint("attempts >= 0", name="ck_jobs_attempts_non_negative"),
        CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled')",
            name="ck_jobs_status",
        ),
        Index("ix_jobs_status_queued_at", "status", "queued_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    generation: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default=JobStatus.QUEUED.value,
        server_default=JobStatus.QUEUED.value,
    )
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    worker_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    queued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(48), nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(500), nullable=True)


class DocumentTextSegment(TimestampMixin, Base):
    """A paragraph-scoped slice of extracted text, with page provenance."""

    __tablename__ = "document_text_segments"
    __table_args__ = (
        UniqueConstraint("document_id", "ordinal", name="uq_segments_document_ordinal"),
        CheckConstraint("ordinal >= 0", name="ck_segments_ordinal_non_negative"),
        CheckConstraint("char_count > 0", name="ck_segments_char_count_positive"),
        CheckConstraint("page_number IS NULL OR page_number >= 1", name="ck_segments_page_number"),
        CheckConstraint("generation >= 1", name="ck_segments_generation_positive"),
        Index("ix_segments_document_page", "document_id", "page_number"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    generation: Mapped[int] = mapped_column(Integer, nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    page_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    char_count: Mapped[int] = mapped_column(Integer, nullable=False)


class SourceRecord(TimestampMixin, Base):
    """Academic source catalog entry.

    Unknown metadata stays ``NULL``. Nothing is inferred: an absent author is not
    an empty author, and ``verification_status`` is only ``verified`` when a
    human/tool actually confirmed the origin.
    """

    __tablename__ = "source_records"
    __table_args__ = (
        CheckConstraint(
            "publication_year IS NULL OR (publication_year BETWEEN 1000 AND 2100)",
            name="ck_sources_year_range",
        ),
        CheckConstraint("page_count IS NULL OR page_count > 0", name="ck_sources_page_count"),
        CheckConstraint(
            "source_type IN ('user_upload', 'lecture_notes', 'book', 'journal_article',"
            " 'thesis', 'report', 'legal_text', 'website', 'other')",
            name="ck_sources_type",
        ),
        CheckConstraint(
            "provenance IN ('user_supplied', 'document_extracted', 'external_verified')",
            name="ck_sources_provenance",
        ),
        CheckConstraint(
            "verification_status IN ('unverified', 'user_asserted', 'verified')",
            name="ck_sources_verification",
        ),
        CheckConstraint(
            "doi IS NULL OR doi ~* '^10\\.\\d{4,9}/\\S+$'", name="ck_sources_doi_shape"
        ),
        CheckConstraint("url IS NULL OR url ~* '^https?://'", name="ck_sources_url_scheme"),
        CheckConstraint(
            # A verified record must say where verification came from.
            "verification_status <> 'verified' OR verified_by IS NOT NULL",
            name="ck_sources_verified_requires_source",
        ),
        # Deduplication is enforced here rather than by an application-level
        # pre-insert lookup, which two concurrent requests can both pass.
        # Partial: sources with no canonical URL have no identity to clash on.
        Index(
            "uq_sources_owner_url_normalized",
            "owner_id",
            "url_normalized",
            unique=True,
            postgresql_where=text("url_normalized IS NOT NULL"),
        ),
        Index("ix_sources_owner_created", "owner_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    document_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("documents.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    title: Mapped[str | None] = mapped_column(String(500), nullable=True)
    authors: Mapped[list[str] | None] = mapped_column(JsonVariant, nullable=True)
    institution: Mapped[str | None] = mapped_column(String(255), nullable=True)
    publisher: Mapped[str | None] = mapped_column(String(255), nullable=True)
    publication_year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_type: Mapped[str] = mapped_column(
        String(24),
        nullable=False,
        default=SourceType.USER_UPLOAD.value,
        server_default=SourceType.USER_UPLOAD.value,
    )
    doi: Mapped[str | None] = mapped_column(String(255), nullable=True)
    isbn: Mapped[str | None] = mapped_column(String(32), nullable=True)
    url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    # Canonical identity derived from ``url`` by services.sources.canonical.
    # The original URL is always preserved; this column exists only to make
    # duplicates detectable atomically. NULL means no identity could be
    # established, which is not the same as "no URL".
    url_normalized: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    repository_identifier: Mapped[str | None] = mapped_column(String(255), nullable=True)
    language: Mapped[str | None] = mapped_column(String(8), nullable=True)
    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    provenance: Mapped[str] = mapped_column(
        String(24),
        nullable=False,
        default=ProvenanceLevel.USER_SUPPLIED.value,
        server_default=ProvenanceLevel.USER_SUPPLIED.value,
    )
    verification_status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=VerificationStatus.UNVERIFIED.value,
        server_default=VerificationStatus.UNVERIFIED.value,
    )
    verified_by: Mapped[str | None] = mapped_column(String(120), nullable=True)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    parser_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    parser_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


# ---------------------------------------------------------------------------
# Phase 5: tutoring. Every table is owned by a user and every grounded artifact
# keeps the provenance it was built from.
# ---------------------------------------------------------------------------


class StudyTopic(TimestampMixin, Base):
    """A user-owned study workspace.

    A topic binds together the material a student is studying and everything
    derived from it: sessions, questions, flashcards, plan items. Ownership is
    the isolation boundary; there is no shared or public topic.
    """

    __tablename__ = "study_topics"
    __table_args__ = (
        CheckConstraint("language IN ('ar', 'fr')", name="ck_study_topics_language"),
        Index("ix_study_topics_owner", "owner_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    language: Mapped[str] = mapped_column(String(2), nullable=False, default="ar")
    archived: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class StudyTopicMaterial(TimestampMixin, Base):
    """A document or source attached to a topic.

    At most one of ``document_id`` / ``source_id`` is set, and exactly one when
    the link is created. The link is kept even after the target is deleted: a
    study artifact must not silently start pointing at someone else's material,
    so both columns go NULL and the reference is reported as unavailable rather
    than resolved to anything else.
    """

    __tablename__ = "study_topic_materials"
    __table_args__ = (
        # AT MOST one target, not exactly one. An "exactly one" check here would
        # contradict ON DELETE SET NULL: nulling the deleted target leaves both
        # columns NULL, and the constraint would then reject the very UPDATE the
        # foreign key fires -- deleting any attached document would fail. Both
        # NULL is the tombstone state meaning "the target was deleted". Exactly
        # one is enforced in the service at creation time.
        CheckConstraint(
            "NOT (document_id IS NOT NULL AND source_id IS NOT NULL)",
            name="ck_materials_at_most_one_target",
        ),
        UniqueConstraint("topic_id", "document_id", "source_id", name="uq_materials_topic_target"),
        Index("ix_materials_topic", "topic_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    topic_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("study_topics.id", ondelete="CASCADE"), nullable=False
    )
    # SET NULL on purpose: the material row survives so derived study artifacts
    # can report "this source is no longer available" instead of lying.
    document_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("documents.id", ondelete="SET NULL"), nullable=True
    )
    source_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("source_records.id", ondelete="SET NULL"), nullable=True
    )


class StudySession(TimestampMixin, Base):
    """A saved, source-grounded exchange inside a topic.

    ``citations`` stores the locators the answer was built from, and
    ``status`` records whether the evidence was sufficient. An answer is never
    stored without its provenance.
    """

    __tablename__ = "study_sessions"
    __table_args__ = (
        CheckConstraint(
            "status IN ('grounded', 'insufficient_evidence', 'no_evidence_available')",
            name="ck_sessions_status",
        ),
        Index("ix_sessions_topic", "topic_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    topic_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("study_topics.id", ondelete="CASCADE"), nullable=False
    )
    question: Mapped[str] = mapped_column(Text, nullable=False)
    answer: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    evidence_sufficient: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    coverage: Mapped[float] = mapped_column(sa_Float, nullable=False, default=0.0)
    citations: Mapped[list[dict[str, object]]] = mapped_column(JSONB, nullable=False, default=list)


class StudyQuestion(TimestampMixin, Base):
    """A question generated from a topic's own material.

    ``source_segment_id`` records which passage the question was built from, so
    an answer can be checked against the material rather than against a model's
    opinion. It is nullable: a question whose source passage has since been
    deleted keeps the row and reports the reference as unavailable.
    """

    __tablename__ = "study_questions"
    __table_args__ = (
        CheckConstraint(
            "difficulty IN ('recall', 'comprehension', 'application')",
            name="ck_questions_difficulty",
        ),
        Index("ix_questions_topic", "topic_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    topic_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("study_topics.id", ondelete="CASCADE"), nullable=False
    )
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    difficulty: Mapped[str] = mapped_column(String(32), nullable=False, default="recall")
    reference_answer: Mapped[str] = mapped_column(Text, nullable=False)
    # SET NULL: the question outlives its source passage and says so.
    source_segment_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey("document_text_segments.id", ondelete="SET NULL"),
        nullable=True,
    )
    source_document_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    source_page_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_ordinal: Mapped[int | None] = mapped_column(Integer, nullable=True)
    answer_submitted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    submitted_answer: Mapped[str | None] = mapped_column(Text, nullable=True)
    # What the comparison could actually establish, kept explicit: which parts of
    # the submission were supported by the reference passage and which were not.
    feedback: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)
    answered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Flashcard(TimestampMixin, Base):
    """A spaced-repetition card with the provenance of its answer.

    Scheduling state (``ease_factor``, ``interval_days``, ``repetitions``,
    ``due_at``) implements the SM-2 algorithm; see
    ``services/study/scheduling.py`` for the exact rules and their limits.
    """

    __tablename__ = "flashcards"
    __table_args__ = (
        CheckConstraint("ease_factor >= 1.3", name="ck_flashcards_ease_floor"),
        CheckConstraint("interval_days >= 0", name="ck_flashcards_interval"),
        CheckConstraint("repetitions >= 0", name="ck_flashcards_repetitions"),
        Index("ix_flashcards_topic_due", "topic_id", "due_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    topic_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("study_topics.id", ondelete="CASCADE"), nullable=False
    )
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    answer: Mapped[str] = mapped_column(Text, nullable=False)
    # Provenance of the answer. Nullable because a source may be deleted later;
    # the card keeps the last known locator and reports it as unverified.
    source_document_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    source_title: Mapped[str | None] = mapped_column(String(300), nullable=True)
    source_page_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_ordinal: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_available: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    ease_factor: Mapped[float] = mapped_column(sa_Float, nullable=False, default=2.5)
    interval_days: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    repetitions: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    review_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class FlashcardReview(TimestampMixin, Base):
    """One review of one card. Append-only history."""

    __tablename__ = "flashcard_reviews"
    __table_args__ = (
        CheckConstraint("rating BETWEEN 0 AND 5", name="ck_reviews_rating"),
        Index("ix_reviews_card", "flashcard_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    flashcard_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("flashcards.id", ondelete="CASCADE"), nullable=False
    )
    rating: Mapped[int] = mapped_column(Integer, nullable=False)
    interval_before_days: Mapped[int] = mapped_column(Integer, nullable=False)
    interval_after_days: Mapped[int] = mapped_column(Integer, nullable=False)


class StudyPlanItem(TimestampMixin, Base):
    """One objective in a user-defined study plan."""

    __tablename__ = "study_plan_items"
    __table_args__ = (
        CheckConstraint(
            "state IN ('not_started', 'in_progress', 'completed', 'blocked')",
            name="ck_plan_items_state",
        ),
        CheckConstraint(
            "state <> 'completed' OR completed_at IS NOT NULL",
            name="ck_plan_items_completed_at",
        ),
        Index("ix_plan_items_topic", "topic_id", "scheduled_for"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    topic_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("study_topics.id", ondelete="CASCADE"), nullable=False
    )
    objective: Mapped[str] = mapped_column(Text, nullable=False)
    scheduled_for: Mapped[date] = mapped_column(Date, nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False, default="not_started")
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Recorded so a plan item can say "the material behind this is gone" instead
    # of silently looking actionable.
    material_note: Mapped[str | None] = mapped_column(String(300), nullable=True)
