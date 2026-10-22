"""Unit tests for upload validation and filename safety."""

from __future__ import annotations

import io
import zipfile

import pytest

from mafhum_api.core.errors import FileRejectedError
from mafhum_api.services.documents import validators
from mafhum_api.services.documents.validators import (
    ValidationLimits,
    check_archive_shape,
    check_extension_and_mime,
    check_signature,
    check_size,
    sanitize_display_name,
)
from tests.fixtures_content import (
    make_docx,
    make_many_entry_zip,
    make_not_a_docx_zip,
    make_traversal_zip,
    make_zip_bomb,
)


def limits() -> ValidationLimits:
    return ValidationLimits(
        max_upload_bytes=1024 * 1024,
        max_pdf_pages=5,
        max_docx_entries=200,
        max_docx_uncompressed_bytes=4 * 1024 * 1024,
        max_archive_compression_ratio=100.0,
    )


class TestExtensionAndMime:
    @pytest.mark.parametrize("name", ["a.pdf", "a.PDF", "a.Pdf"])
    def test_accepts_pdf_extensions(self, name: str) -> None:
        assert check_extension_and_mime(name, "application/pdf") == "pdf"

    def test_accepts_docx(self) -> None:
        mime = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        assert check_extension_and_mime("a.docx", mime) == "docx"

    def test_rejects_unsupported_extension(self) -> None:
        with pytest.raises(FileRejectedError) as info:
            check_extension_and_mime("evil.exe", "application/x-msdownload")
        assert info.value.details is not None
        assert info.value.details["reason"] == validators.REASON_UNSUPPORTED_EXTENSION

    @pytest.mark.parametrize("name", ["a.exe", "a.txt", "a.html", "a.js", "a.py", "a.sh", "a"])
    def test_rejects_other_extensions(self, name: str) -> None:
        with pytest.raises(FileRejectedError):
            check_extension_and_mime(name, "application/octet-stream")

    def test_rejects_extension_mime_mismatch(self) -> None:
        with pytest.raises(FileRejectedError) as info:
            check_extension_and_mime("a.pdf", "image/png")
        assert info.value.details is not None
        assert info.value.details["reason"] == validators.REASON_SIGNATURE_MISMATCH

    def test_missing_mime_is_tolerated(self) -> None:
        # Browsers omit it sometimes; the byte-level check still applies.
        assert check_extension_and_mime("a.pdf", None) == "pdf"

    def test_mime_parameters_are_ignored(self) -> None:
        assert check_extension_and_mime("a.pdf", "application/pdf; charset=binary") == "pdf"


class TestSignature:
    def test_pdf_signature(self) -> None:
        assert check_signature(b"%PDF-1.7\nrest", "pdf") == "pdf"

    def test_zip_signature_maps_to_docx(self) -> None:
        assert check_signature(b"PK\x03\x04rest", "docx") == "docx"

    def test_empty_is_rejected(self) -> None:
        with pytest.raises(FileRejectedError) as info:
            check_signature(b"", "pdf")
        assert info.value.details is not None
        assert info.value.details["reason"] == validators.REASON_EMPTY

    def test_png_named_pdf_is_rejected(self) -> None:
        png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
        with pytest.raises(FileRejectedError) as info:
            check_signature(png, "pdf")
        assert info.value.details is not None
        assert info.value.details["reason"] == validators.REASON_SIGNATURE_MISMATCH

    def test_pdf_bytes_named_docx_is_rejected(self) -> None:
        with pytest.raises(FileRejectedError):
            check_signature(b"%PDF-1.7\nrest", "docx")

    def test_legacy_office_document_is_rejected_with_hint(self) -> None:
        ole = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 32
        with pytest.raises(FileRejectedError) as info:
            check_signature(ole, "docx")
        assert info.value.details is not None
        assert info.value.details["hint"] == "legacy_office_not_supported"

    def test_rtf_is_rejected_with_hint(self) -> None:
        with pytest.raises(FileRejectedError) as info:
            check_signature(b"{\\rtf1\\ansi}", "docx")
        assert info.value.details is not None
        assert info.value.details["hint"] == "rtf_not_supported"

    def test_declaration_missing_still_detects(self) -> None:
        assert check_signature(b"%PDF-1.7\nrest", "") == "pdf"


class TestSize:
    def test_zero_rejected(self) -> None:
        with pytest.raises(FileRejectedError):
            check_size(0, limits())

    def test_negative_rejected(self) -> None:
        with pytest.raises(FileRejectedError):
            check_size(-1, limits())

    def test_over_limit_rejected(self) -> None:
        with pytest.raises(FileRejectedError) as info:
            check_size(2 * 1024 * 1024, limits())
        assert info.value.details is not None
        assert info.value.details["reason"] == validators.REASON_TOO_LARGE

    def test_within_limit_ok(self) -> None:
        check_size(1024, limits())


class TestFilenameSafety:
    def test_plain_name_kept(self) -> None:
        assert sanitize_display_name("درس.pdf") == "درس.pdf"

    def test_arabic_name_kept_verbatim(self) -> None:
        name = "بحث_التخرج_2026.pdf"
        assert sanitize_display_name(name) == name

    @pytest.mark.parametrize(
        "payload",
        [
            "../../../etc/passwd",
            "..\\..\\windows\\system32\\x.pdf",
            "/etc/passwd",
            "C:\\Windows\\evil.pdf",
            "....//....//x.pdf",
        ],
    )
    def test_traversal_is_neutralized_to_final_component(self, payload: str) -> None:
        result = sanitize_display_name(payload)
        assert "/" not in result
        assert "\\" not in result
        assert ".." not in result

    @pytest.mark.parametrize("payload", ["", "   ", ".", "..", "/", "\\", "...", "\x00.pdf"])
    def test_unsafe_names_rejected(self, payload: str) -> None:
        with pytest.raises(FileRejectedError) as info:
            sanitize_display_name(payload)
        assert info.value.details is not None
        assert info.value.details["reason"] == validators.REASON_FILENAME_UNSAFE

    def test_control_characters_rejected(self) -> None:
        # A legitimate filename never contains control characters; rejecting them
        # surfaces truncation-injection attempts instead of silently rewriting.
        with pytest.raises(FileRejectedError):
            sanitize_display_name("a\x00b\x1fc.pdf")

    def test_null_byte_injection_rejected(self) -> None:
        with pytest.raises(FileRejectedError):
            sanitize_display_name("safe.pdf\x00.exe")

    def test_shell_metacharacters_are_stripped(self) -> None:
        result = sanitize_display_name("a<b>c|d?.pdf")
        assert result == "abcd.pdf"

    def test_long_name_truncated_preserving_extension(self) -> None:
        result = sanitize_display_name("a" * 500 + ".pdf")
        assert len(result) <= 200
        assert result.endswith(".pdf")


class TestArchiveShape:
    def _write(self, data: bytes, tmp_path, name: str = "a.docx"):  # type: ignore[no-untyped-def]
        path = tmp_path / name
        path.write_bytes(data)
        return path

    def test_valid_docx_accepted(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        path = self._write(make_docx("arabic"), tmp_path)
        check_archive_shape(path, limits())

    def test_zip_bomb_rejected_without_decompressing(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        path = self._write(make_zip_bomb(), tmp_path)
        with pytest.raises(FileRejectedError) as info:
            check_archive_shape(path, limits())
        assert info.value.details is not None
        assert info.value.details["reason"] == validators.REASON_ARCHIVE_BOMB

    def test_excessive_entry_count_rejected(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        path = self._write(make_many_entry_zip(500), tmp_path)
        with pytest.raises(FileRejectedError) as info:
            check_archive_shape(path, limits())
        assert info.value.details is not None
        assert info.value.details["reason"] == validators.REASON_TOO_MANY_ENTRIES

    def test_traversal_member_name_rejected(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        path = self._write(make_traversal_zip(), tmp_path)
        with pytest.raises(FileRejectedError) as info:
            check_archive_shape(path, limits())
        assert info.value.details is not None
        assert info.value.details["hint"] == "unsafe_member_name"

    def test_zip_without_docx_parts_rejected(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        path = self._write(make_not_a_docx_zip(), tmp_path)
        with pytest.raises(FileRejectedError) as info:
            check_archive_shape(path, limits())
        assert info.value.details is not None
        assert info.value.details["hint"] == "not_a_docx_container"

    def test_corrupt_zip_rejected(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        path = self._write(b"PK\x03\x04" + b"garbage" * 50, tmp_path)
        with pytest.raises(FileRejectedError) as info:
            check_archive_shape(path, limits())
        assert info.value.details is not None
        assert info.value.details["reason"] == validators.REASON_MALFORMED

    def test_empty_zip_rejected(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w"):
            pass
        path = self._write(buffer.getvalue(), tmp_path)
        with pytest.raises(FileRejectedError):
            check_archive_shape(path, limits())
