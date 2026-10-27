"""The EICAR probe must be one the SCANNER can reject, not one validation rejects.

This test exists because of a genuine false positive in the staging verification
procedure. MAFHUM validates extension, MIME type and magic bytes before it scans
stored bytes, so the original check -- upload a bare ``eicar.com`` declared as
``application/pdf`` and assert HTTP 422 -- would have been rejected by validation
and never reached the scanner. The check would have printed PASS on an engine that
was never consulted, which is the worst possible outcome for the one control that
matters most.

So these tests assert the property the probe actually needs: the bare EICAR file IS
rejected before scanning (proving the false positive was real), while the PDF probe
survives validation and can therefore only be rejected by the scanner.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from tools.eicar_probe import EICAR, PAYLOAD_MEMBER, build_eicar_docx

from mafhum_api.core.errors import FileRejectedError
from mafhum_api.db.models import DocumentFormat
from mafhum_api.services.documents import validators

PDF_MIME = "application/pdf"
DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

# The script lives at the repository root, while tests run from apps/api.
SCRIPT = Path(__file__).resolve().parents[3] / "tools" / "staging_verification.sh"


def _limits() -> validators.ValidationLimits:
    """The application's default document limits, for structural validation."""
    return validators.ValidationLimits(
        max_upload_bytes=20 * 1024 * 1024,
        max_pdf_pages=200,
        max_docx_entries=400,
        max_docx_uncompressed_bytes=60 * 1024 * 1024,
        max_archive_compression_ratio=100.0,
    )


class TestTheFalsePositiveWasReal:
    @pytest.mark.skipif(
        sys.platform == "win32",
        reason=(
            "the machine's own antivirus quarantines a bare EICAR file on disk "
            "between the write and the read-back; the property stays asserted "
            "on POSIX (CI runs on Linux)"
        ),
    )
    def test_a_bare_eicar_file_is_rejected_by_validation_before_scanning(
        self, tmp_path: Path
    ) -> None:
        """The probe the script originally used never reached the scanner."""
        bare = tmp_path / "eicar.com"
        bare.write_bytes(EICAR.encode("latin-1"))
        head = bare.read_bytes()[:16]

        # Extension/MIME mismatch: .com is not a supported document type.
        with pytest.raises(FileRejectedError):
            validators.check_extension_and_mime(bare.name, PDF_MIME)

        # And even under a .pdf name the magic bytes are wrong, so it is still
        # rejected structurally. Both rejections precede any scan.
        with pytest.raises(FileRejectedError):
            validators.check_signature(head, DocumentFormat.PDF.value)


class TestTheDocxProbeReachesTheScanner:
    """The probe is a DOCX because ClamAV, by design, does not match EICAR
    embedded inside other files' bytes — only the file itself, or a payload
    inside an archive it unwraps. A DOCX is a ZIP, which the engine unwraps;
    see tools/eicar_probe.py for the acceptance evidence behind this.
    """

    def test_the_probe_is_a_valid_docx_carrying_eicar(self, tmp_path: Path) -> None:
        import zipfile

        probe = build_eicar_docx(tmp_path / "probe.docx")
        data = probe.read_bytes()

        assert data.startswith(b"PK\x03\x04"), "must satisfy the ZIP magic-byte check"
        with zipfile.ZipFile(probe) as archive:
            names = set(archive.namelist())
            assert {"[Content_Types].xml", "word/document.xml", PAYLOAD_MEMBER} <= names
            assert archive.read(PAYLOAD_MEMBER) == EICAR.encode("latin-1")

    def test_the_payload_member_is_stored_uncompressed(self, tmp_path: Path) -> None:
        """The detectable placement: raw payload bytes inside the archive.

        ClamAV returned CLEAN for the same string embedded in a PDF content
        stream AND appended raw after %%EOF (runs on c72ff67 / ea712e8,
        2026-10-25) while detecting a bare EICAR file. It detects payloads
        inside archives it unwraps, so the member must carry the exact
        contiguous bytes, stored (not deflated).
        """
        import zipfile

        probe = build_eicar_docx(tmp_path / "probe.docx")
        with zipfile.ZipFile(probe) as archive:
            info = archive.getinfo(PAYLOAD_MEMBER)
            assert info.compress_type == zipfile.ZIP_STORED
            assert archive.read(PAYLOAD_MEMBER) == EICAR.encode("latin-1")

    def test_the_probe_passes_every_structural_validation(self, tmp_path: Path) -> None:
        """If any of these raised, the scanner would again never be consulted."""
        probe = build_eicar_docx(tmp_path / "probe.docx")

        declared = validators.check_extension_and_mime(probe.name, DOCX_MIME)
        assert declared == DocumentFormat.DOCX.value

        detected = validators.check_signature(probe.read_bytes()[:16], DocumentFormat.DOCX.value)
        assert detected == DocumentFormat.DOCX.value

        validators.check_archive_shape(probe, _limits())

    def test_the_probe_is_parseable_as_a_document(self, tmp_path: Path) -> None:
        """The upload path hands DOCX files to the real parser; the probe must
        survive it like any legitimate document."""
        from docx import Document

        probe = build_eicar_docx(tmp_path / "probe.docx")
        document = Document(str(probe))
        assert document.paragraphs, "the probe must be a real document"

    def test_the_probe_is_small_enough_to_upload(self, tmp_path: Path) -> None:
        probe = build_eicar_docx(tmp_path / "probe.docx")
        assert probe.stat().st_size < 200_000, "probe should be small"

    def test_a_clean_probe_differs_only_in_payload(self, tmp_path: Path) -> None:
        """A control: the same DOCX without EICAR must be byte-wise different.

        Without this, "the scanner rejected it" could be explained by the
        container itself rather than by the payload.
        """
        import zipfile

        infected = build_eicar_docx(tmp_path / "infected.docx")
        clean = build_eicar_docx(tmp_path / "clean.docx", payload="harmless text")
        assert infected.read_bytes() != clean.read_bytes()
        with zipfile.ZipFile(clean) as archive:
            assert EICAR.encode("latin-1") not in archive.read(PAYLOAD_MEMBER)


class TestScriptIntegrity:
    """The procedure must not be able to report a pass it did not earn."""

    def test_the_script_exists(self) -> None:
        assert SCRIPT.is_file(), f"missing {SCRIPT}"

    def test_the_script_asserts_the_scanner_reason_not_just_http_422(self) -> None:
        script = SCRIPT.read_text()
        assert '"reason":"malware_detected"' in script, (
            "the script must assert the scanner's reason; a bare 422 is also "
            "returned by validation and would be a false positive"
        )

    def test_the_script_checks_the_stop_command_result(self) -> None:
        """Regression for the unconditional PASS after `$runtime stop`."""
        script = SCRIPT.read_text()
        assert "if $runtime stop -t 15" in script, "stop result must be checked"
        assert "{{.State.ExitCode}}" in script, "container exit state must be checked"
        assert "--rm \\\n" not in script, (
            "--rm destroys the container before its exit state can be inspected"
        )

    def test_the_script_checks_signature_database_freshness(self) -> None:
        """An engine with stale or absent signatures reports everything clean."""
        script = SCRIPT.read_text()
        assert "*.cvd" in script, "must look for actual signature database files"
        assert "clamdtop --version >/dev/null 2>&1 &&" not in script, (
            "testing whether clamdtop is installed says nothing about the database"
        )

        # Assert the POLICY, not one implementation of it. A `.cdb` is an
        # auxiliary file and cannot satisfy the core-database requirement, and
        # freshness must come from the NEWEST core database -- counting every file
        # older than the threshold fails wrongly when a fresh database coexists
        # with an old one.
        assert "604800" in script, "the seven-day threshold must be explicit (7*86400)"
        assert "*.cld" in script, "a .cld must also count as a core database"
        assert "core_db_count" in script, "must require a core database specifically"
        assert "newest_db_mtime" in script, "freshness must use the newest core database"
        assert "sort -nr" in script, "must select the newest, not any, database"

    def test_signature_freshness_uses_newest_core_database(self) -> None:
        """An old auxiliary file must not make a fresh core database fail."""
        script = SCRIPT.read_text()
        assert "core_db_count" in script, "must require a core .cvd/.cld database"
        assert "sort -nr" in script, "must assess the newest database timestamp"
        assert "604800" in script, "seven-day freshness threshold must be explicit"
        assert "-mtime +7" not in script, (
            "counting every old file can fail even when a fresh database exists"
        )

    def test_missing_container_secrets_are_reported_as_blocked(self) -> None:
        """Missing config must not terminate before the structured summary."""
        script = SCRIPT.read_text()
        assert 'if [ -z "${MAFHUM_STAGING_DATABASE_URL:-}" ]' in script
        assert "staging database URL or session secret is not configured" in script
        assert "${MAFHUM_STAGING_DATABASE_URL:?" not in script
        assert "${MAFHUM_STAGING_SESSION_SECRET:?" not in script

    def test_eicar_probe_is_generated_once(self) -> None:
        script = SCRIPT.read_text()
        assert script.count('pass "built a structurally valid DOCX carrying the EICAR string"') == 1

    def test_the_script_never_disables_tls_verification(self) -> None:
        script = SCRIPT.read_text()
        assert "verify=False" not in script
        assert "CERT_NONE" not in script
        assert "-k " not in script, "curl -k would disable certificate verification"
        assert "--insecure" not in script

    def test_the_script_requires_credentials_before_uploading(self) -> None:
        """Must BLOCK rather than send an unauthenticated request that 401s."""
        script = SCRIPT.read_text()
        assert 'if [ -z "${MAFHUM_CSRF_TOKEN:-}" ]' in script

    def test_the_script_never_echoes_a_secret(self) -> None:
        """Credentials are used but must not appear in output or evidence files."""
        script = SCRIPT.read_text()
        assert 'echo "$MAFHUM_COOKIE"' not in script
        assert 'echo "$MAFHUM_CSRF_TOKEN"' not in script
        assert 'echo "$MAFHUM_STAGING_DATABASE_URL"' not in script

    def test_the_script_exits_non_zero_when_blocked(self) -> None:
        """A sign-off that silently skipped a control is worse than one that stopped."""
        script = SCRIPT.read_text()
        assert "FAILURES=$((FAILURES + 1))" in script
        assert "exit 1" in script
