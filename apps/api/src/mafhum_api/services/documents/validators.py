"""Server-side validation of untrusted uploads.

Every uploaded file is treated as hostile. Three independent signals must agree
before a file is accepted:

1. **Declared name** — extension allowlist (cheap, cosmetic).
2. **Declared MIME** — the browser's claim; never trusted on its own.
3. **Actual bytes** — magic-number sniffing plus a real parse attempt by the
   format's own parser.

Anything else that could be checked without executing the file is also checked:
archive entry counts, uncompressed size and compression ratio (zip bombs), page
counts, and encrypted/malformed structure.

Nothing here is malware detection. There is no AV engine in this deployment and
this module does not claim to be one — see ``docs/THREAT_MODEL.md``.
"""

from __future__ import annotations

import re
import unicodedata
import zipfile
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Final

from mafhum_api.core.errors import FileRejectedError

# --- rejection reasons (machine readable, surfaced in Arabic by the UI) -------
REASON_EMPTY: Final = "empty_file"
REASON_TOO_LARGE: Final = "file_too_large"
REASON_UNSUPPORTED_EXTENSION: Final = "unsupported_extension"
REASON_UNSUPPORTED_TYPE: Final = "unsupported_content_type"
REASON_SIGNATURE_MISMATCH: Final = "signature_mismatch"
REASON_MALFORMED: Final = "malformed_file"
REASON_ENCRYPTED: Final = "encrypted_file"
REASON_ARCHIVE_BOMB: Final = "archive_expansion_too_large"
REASON_TOO_MANY_ENTRIES: Final = "archive_entry_count_too_large"
REASON_TOO_MANY_PAGES: Final = "too_many_pages"
REASON_FILENAME_UNSAFE: Final = "unsafe_filename"

# Human-readable Arabic messages. Kept free of any input echo so an error can
# never reflect attacker-controlled content back into a response.
ARABIC_MESSAGES: Final[dict[str, str]] = {
    REASON_EMPTY: "الملف فارغ.",
    REASON_TOO_LARGE: "حجم الملف يتجاوز الحد المسموح به.",
    REASON_UNSUPPORTED_EXTENSION: "صيغة الملف غير مدعومة. الصيغ المدعومة: PDF و DOCX.",
    REASON_UNSUPPORTED_TYPE: "نوع الملف غير مدعوم.",
    REASON_SIGNATURE_MISMATCH: "محتوى الملف لا يطابق صيغته المعلنة.",
    REASON_MALFORMED: "الملف تالف أو غير قابل للقراءة.",
    REASON_ENCRYPTED: "الملف محمي بكلمة مرور ولا يمكن معالجته.",
    REASON_ARCHIVE_BOMB: "الملف يحتوي على توسّع غير طبيعي للبيانات.",
    REASON_TOO_MANY_ENTRIES: "الملف يحتوي على عدد مفرط من العناصر الداخلية.",
    REASON_TOO_MANY_PAGES: "عدد صفحات المستند يتجاوز الحد المسموح به.",
    REASON_FILENAME_UNSAFE: "اسم الملف غير صالح.",
}

PDF_SIGNATURE: Final = b"%PDF-"
ZIP_SIGNATURE: Final = b"PK\x03\x04"
# Empty ZIP archives (EOCD only) still need to be rejected as malformed DOCX.
ZIP_EMPTY_SIGNATURE: Final = b"PK\x05\x06"
OLE_SIGNATURE: Final = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
RTF_SIGNATURE: Final = b"{\\rtf"
EXTENSION_BY_FORMAT: Final[dict[str, str]] = {"pdf": "pdf", "docx": "docx"}
ALLOWED_EXTENSIONS: Final[frozenset[str]] = frozenset({".pdf", ".docx"})
MIME_BY_FORMAT: Final[dict[str, frozenset[str]]] = {
    # ``application/octet-stream`` is accepted for both formats because browsers
    # and curl send it whenever they cannot determine a type. It is never
    # sufficient on its own: the magic-byte check below is authoritative.
    "pdf": frozenset({"application/pdf", "application/octet-stream"}),
    "docx": frozenset(
        {
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "application/zip",
            "application/octet-stream",
        }
    ),
}

_MAX_DISPLAY_NAME = 200
# Control characters and anything the filesystem/HTTP layer treats specially.
_UNSAFE_NAME_RE = re.compile(r'[\x00-\x1f\x7f<>:"/\\|?*]')
_CONTROL_CHAR_RE = re.compile(r"[\x00-\x1f\x7f]")
_TRAVERSAL_SEGMENTS = frozenset({"", ".", "..", "~"})


@dataclass(frozen=True, slots=True)
class ValidationLimits:
    """The subset of application limits validation needs."""

    max_upload_bytes: int
    max_pdf_pages: int
    max_docx_entries: int
    max_docx_uncompressed_bytes: int
    max_archive_compression_ratio: float


def detect_format(head: bytes) -> str | None:
    """Return ``'pdf'``/``'docx'`` from magic bytes, or ``None`` if unknown."""
    if head.startswith(PDF_SIGNATURE):
        return "pdf"
    if head.startswith(ZIP_SIGNATURE) or head.startswith(ZIP_EMPTY_SIGNATURE):
        # A ZIP is only a DOCX if it carries the Word content-type part; that is
        # checked later by the parser. Treat it as a DOCX candidate for now.
        return "docx"
    return None


def looks_like_legacy_office(head: bytes) -> bool:
    """True for the legacy OLE2 container used by .doc/.xls/.ppt."""
    return head.startswith(OLE_SIGNATURE)


def extension_of(filename: str) -> str:
    return PurePosixPath(filename.replace("\\", "/")).suffix.lower()


def _has_meaningful_char(name: str) -> bool:
    """True when at least one letter or digit survives sanitization.

    Rejects names that consisted only of separators, dots and control characters,
    which would otherwise be silently reduced to something meaningless.
    """
    return any(ch.isalnum() for ch in name)


def sanitize_display_name(filename: str) -> str:
    """Reduce a user-supplied filename to a safe, presentable label.

    The result is used only for display and ``Content-Disposition``; it is never
    used to build a filesystem path. Path separators are stripped, control
    characters removed and the name is NFC-normalised so Arabic text renders
    consistently.
    """
    if not isinstance(filename, str) or not filename.strip():
        raise FileRejectedError(
            ARABIC_MESSAGES[REASON_FILENAME_UNSAFE], details={"reason": REASON_FILENAME_UNSAFE}
        )
    # Take the final component only: defeats both ../ traversal and
    # C:\Windows-style absolute paths arriving in the filename field.
    name = filename.replace("\\", "/").rsplit("/", 1)[-1]
    name = unicodedata.normalize("NFC", name)
    # Control characters (including a null byte) never appear in a legitimate
    # filename. Reject rather than silently rewriting: a null byte is the classic
    # truncation-injection vector and rewriting would hide the attempt.
    if _CONTROL_CHAR_RE.search(name):
        raise FileRejectedError(
            ARABIC_MESSAGES[REASON_FILENAME_UNSAFE], details={"reason": REASON_FILENAME_UNSAFE}
        )
    name = _UNSAFE_NAME_RE.sub("", name)
    name = name.strip().strip(".")
    if not name or name in _TRAVERSAL_SEGMENTS or not _has_meaningful_char(name):
        raise FileRejectedError(
            ARABIC_MESSAGES[REASON_FILENAME_UNSAFE], details={"reason": REASON_FILENAME_UNSAFE}
        )
    if len(name) > _MAX_DISPLAY_NAME:
        stem, _, suffix = name.rpartition(".")
        if stem and len(suffix) <= 8:
            name = f"{stem[: _MAX_DISPLAY_NAME - len(suffix) - 1]}.{suffix}"
        else:
            name = name[:_MAX_DISPLAY_NAME]
    if not name.strip():
        raise FileRejectedError(
            ARABIC_MESSAGES[REASON_FILENAME_UNSAFE], details={"reason": REASON_FILENAME_UNSAFE}
        )
    return name


def check_extension_and_mime(filename: str, content_type: str | None) -> str:
    """Cross-check the declared extension against the declared MIME type.

    Returns the declared format key. A mismatch between extension and MIME is a
    hard rejection: honest clients do not produce it, and it is a cheap filter
    for files renamed to dodge an allowlist.
    """
    ext = extension_of(filename)
    if ext not in ALLOWED_EXTENSIONS:
        raise FileRejectedError(
            ARABIC_MESSAGES[REASON_UNSUPPORTED_EXTENSION],
            details={"reason": REASON_UNSUPPORTED_EXTENSION, "allowed": sorted(ALLOWED_EXTENSIONS)},
        )
    declared = ext[1:]
    expected_mimes = MIME_BY_FORMAT[declared]
    normalized = (content_type or "").split(";")[0].strip().lower()
    if normalized and normalized not in expected_mimes:
        raise FileRejectedError(
            ARABIC_MESSAGES[REASON_SIGNATURE_MISMATCH],
            details={"reason": REASON_SIGNATURE_MISMATCH, "expected_format": declared},
        )
    return declared


def check_signature(head: bytes, declared_format: str) -> str:
    """Confirm the leading bytes match the declared format.

    Returns the *detected* format, which may differ from the declaration when the
    declaration was missing but the bytes are unambiguous.
    """
    if not head:
        raise FileRejectedError(ARABIC_MESSAGES[REASON_EMPTY], details={"reason": REASON_EMPTY})
    detected = detect_format(head)
    if detected is None:
        if looks_like_legacy_office(head):
            raise FileRejectedError(
                ARABIC_MESSAGES[REASON_UNSUPPORTED_TYPE],
                details={"reason": REASON_UNSUPPORTED_TYPE, "hint": "legacy_office_not_supported"},
            )
        if head.startswith(RTF_SIGNATURE):
            raise FileRejectedError(
                ARABIC_MESSAGES[REASON_UNSUPPORTED_TYPE],
                details={"reason": REASON_UNSUPPORTED_TYPE, "hint": "rtf_not_supported"},
            )
        raise FileRejectedError(
            ARABIC_MESSAGES[REASON_SIGNATURE_MISMATCH],
            details={"reason": REASON_SIGNATURE_MISMATCH},
        )
    if declared_format and detected != declared_format:
        raise FileRejectedError(
            ARABIC_MESSAGES[REASON_SIGNATURE_MISMATCH],
            details={
                "reason": REASON_SIGNATURE_MISMATCH,
                "declared": declared_format,
                "detected": detected,
            },
        )
    return detected


def check_size(size_bytes: int, limits: ValidationLimits) -> None:
    if size_bytes <= 0:
        raise FileRejectedError(ARABIC_MESSAGES[REASON_EMPTY], details={"reason": REASON_EMPTY})
    if size_bytes > limits.max_upload_bytes:
        raise FileRejectedError(
            ARABIC_MESSAGES[REASON_TOO_LARGE],
            details={
                "reason": REASON_TOO_LARGE,
                "max_bytes": limits.max_upload_bytes,
            },
        )


def check_archive_shape(
    path_or_bytes: object,
    limits: ValidationLimits,
    *,
    required_parts: Sequence[str] = ("[Content_Types].xml", "word/document.xml"),
) -> None:
    """Validate a ZIP-based container without decompressing anything.

    Uses only the central directory metadata, so a bomb cannot spend CPU or disk
    during validation. Checks entry count, total uncompressed size, the
    compressed-to-uncompressed ratio, per-entry ratio and path sanity.
    """
    from pathlib import Path

    source: str | bytes
    if isinstance(path_or_bytes, Path | str):
        source = str(path_or_bytes)
    else:
        raise TypeError("check_archive_shape expects a path")

    try:
        with zipfile.ZipFile(source) as archive:
            infos = archive.infolist()
            if not infos:
                raise FileRejectedError(
                    ARABIC_MESSAGES[REASON_MALFORMED], details={"reason": REASON_MALFORMED}
                )
            if len(infos) > limits.max_docx_entries:
                raise FileRejectedError(
                    ARABIC_MESSAGES[REASON_TOO_MANY_ENTRIES],
                    details={
                        "reason": REASON_TOO_MANY_ENTRIES,
                        "max_entries": limits.max_docx_entries,
                    },
                )
            names = set()
            total_uncompressed = 0
            for info in infos:
                name = info.filename
                # Reject absolute paths and traversal in member names: a parser
                # or extraction helper that trusted them could write anywhere.
                if name.startswith("/") or ".." in PurePosixPath(name).parts:
                    raise FileRejectedError(
                        ARABIC_MESSAGES[REASON_MALFORMED],
                        details={"reason": REASON_MALFORMED, "hint": "unsafe_member_name"},
                    )
                if info.file_size > limits.max_docx_uncompressed_bytes:
                    raise FileRejectedError(
                        ARABIC_MESSAGES[REASON_ARCHIVE_BOMB],
                        details={"reason": REASON_ARCHIVE_BOMB},
                    )
                ratio = (
                    info.file_size / info.compress_size
                    if info.compress_size > 0
                    else float(info.file_size)
                )
                if ratio > limits.max_archive_compression_ratio and info.file_size > 65_536:
                    raise FileRejectedError(
                        ARABIC_MESSAGES[REASON_ARCHIVE_BOMB],
                        details={
                            "reason": REASON_ARCHIVE_BOMB,
                            "hint": "compression_ratio",
                        },
                    )
                total_uncompressed += info.file_size
                names.add(name)
            if total_uncompressed > limits.max_docx_uncompressed_bytes:
                raise FileRejectedError(
                    ARABIC_MESSAGES[REASON_ARCHIVE_BOMB],
                    details={
                        "reason": REASON_ARCHIVE_BOMB,
                        "hint": "total_uncompressed_size",
                        "max_bytes": limits.max_docx_uncompressed_bytes,
                    },
                )
            missing = [part for part in required_parts if part not in names]
            if missing:
                raise FileRejectedError(
                    ARABIC_MESSAGES[REASON_MALFORMED],
                    details={"reason": REASON_MALFORMED, "hint": "not_a_docx_container"},
                )
    except zipfile.BadZipFile as exc:
        raise FileRejectedError(
            ARABIC_MESSAGES[REASON_MALFORMED], details={"reason": REASON_MALFORMED}
        ) from exc
    except FileRejectedError:
        raise
    except OSError as exc:
        raise FileRejectedError(
            ARABIC_MESSAGES[REASON_MALFORMED], details={"reason": REASON_MALFORMED}
        ) from exc


def check_page_count(page_count: int, limits: ValidationLimits) -> None:
    if page_count > limits.max_pdf_pages:
        raise FileRejectedError(
            ARABIC_MESSAGES[REASON_TOO_MANY_PAGES],
            details={"reason": REASON_TOO_MANY_PAGES, "max_pages": limits.max_pdf_pages},
        )


def check_extracted_size(char_count: int, max_chars: int) -> None:
    if char_count > max_chars:
        raise FileRejectedError(
            ARABIC_MESSAGES[REASON_TOO_LARGE],
            details={"reason": REASON_TOO_LARGE, "hint": "extracted_text"},
        )


def iter_head(chunks: Iterable[bytes], size: int) -> tuple[bytes, Iterable[bytes]]:
    """Peek at the first ``size`` bytes without consuming the stream twice."""
    iterator = iter(chunks)
    buffer = bytearray()
    first: bytes | None = None
    while len(buffer) < size:
        try:
            first = next(iterator)
        except StopIteration:
            break
        if not first:
            continue
        buffer.extend(first)
        if len(buffer) >= size:
            break

    def replay() -> Iterable[bytes]:
        if buffer:
            yield bytes(buffer)
        yield from iterator

    return bytes(buffer), replay()
