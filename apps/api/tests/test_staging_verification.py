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

from pathlib import Path

import pytest
from tools.eicar_probe import EICAR, build_eicar_pdf

from mafhum_api.core.errors import FileRejectedError
from mafhum_api.db.models import DocumentFormat
from mafhum_api.services.documents import validators

PDF_MIME = "application/pdf"

# The script lives at the repository root, while tests run from apps/api.
SCRIPT = Path(__file__).resolve().parents[3] / "tools" / "staging_verification.sh"


class TestTheFalsePositiveWasReal:
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


class TestThePdfProbeReachesTheScanner:
    def test_the_probe_is_a_valid_pdf_carrying_eicar(self, tmp_path: Path) -> None:
        probe = build_eicar_pdf(tmp_path / "probe.pdf")
        data = probe.read_bytes()

        assert data.startswith(b"%PDF-"), "must satisfy the PDF magic-byte check"
        assert data.rstrip().endswith(b"%%EOF")
        assert EICAR.encode("latin-1") in data, "the payload must be in the bytes"

    def test_the_probe_passes_every_structural_validation(self, tmp_path: Path) -> None:
        """If any of these raised, the scanner would again never be consulted."""
        probe = build_eicar_pdf(tmp_path / "probe.pdf")

        declared = validators.check_extension_and_mime(probe.name, PDF_MIME)
        assert declared == DocumentFormat.PDF.value

        detected = validators.check_signature(probe.read_bytes()[:16], DocumentFormat.PDF.value)
        assert detected == DocumentFormat.PDF.value

    def test_the_probe_is_parseable_as_one_page(self, tmp_path: Path) -> None:
        """The upload path also enforces a page-count limit via a real parser."""
        probe = build_eicar_pdf(tmp_path / "probe.pdf")
        from pypdf import PdfReader

        reader = PdfReader(str(probe))
        assert len(reader.pages) == 1

    def test_the_probe_is_small_enough_to_upload(self, tmp_path: Path) -> None:
        probe = build_eicar_pdf(tmp_path / "probe.pdf")
        assert probe.stat().st_size < 10_000, "probe should be tiny"

    def test_a_clean_probe_differs_only_in_payload(self, tmp_path: Path) -> None:
        """A control: the same PDF without EICAR must be byte-wise different.

        Without this, "the scanner rejected it" could be explained by the PDF
        structure itself rather than by the payload.
        """
        infected = build_eicar_pdf(tmp_path / "infected.pdf")
        clean = build_eicar_pdf(tmp_path / "clean.pdf", payload="harmless text")
        assert infected.read_bytes() != clean.read_bytes()
        assert EICAR.encode("latin-1") not in clean.read_bytes()


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
        assert "-mtime +7" in script, "must check database freshness"
        assert "clamdtop --version >/dev/null 2>&1 &&" not in script, (
            "testing whether clamdtop is installed says nothing about the database"
        )

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
        assert script.count('pass "built a structurally valid PDF carrying the EICAR string"') == 1

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
