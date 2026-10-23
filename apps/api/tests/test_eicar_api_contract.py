"""The API response shape the staging gate asserts on must actually exist.

``tools/staging_verification.sh`` decides whether Gate A passed by grepping the
upload response for ``"reason":"malware_detected"``. Nothing verified that the
application emits that exact JSON. If it did not, the gate could never pass on a
correctly configured staging host -- the operator would see a rejection that was
real, and a script that called it a failure, with no way to tell which was wrong.

So this pins the contract from the application side, using the same
``eicar_probe`` module the staging script builds its probe from, and the same stub
``clamd`` the rest of the scanning tests use over a real Unix socket.

Boundary, stated plainly: this verifies that **the application** reports
``malware_detected`` when the engine says ``FOUND``. It does not verify that a real
ClamAV engine detects EICAR -- no engine is installable here, and a stub daemon is
not an engine. Gate A's detection claim stays unverified until a real engine is
tested.
"""

from __future__ import annotations

import json
from pathlib import Path

from tools.eicar_probe import EICAR, build_eicar_pdf

from mafhum_api.config import Settings
from mafhum_api.main import create_app
from tests.conftest import _register, upload_kwargs
from tests.test_malware_scanning import FakeClamd

PDF_MIME = "application/pdf"

# The exact substring the staging script greps for. Kept as a constant so the two
# cannot drift: if this test passes, the script's assertion is satisfiable.
SCRIPT_ASSERTION = '"reason":"malware_detected"'


class TestEicarApiContract:
    def test_an_eicar_pdf_upload_reports_reason_malware_detected(
        self, settings, tmp_path: Path
    ) -> None:
        clamd_path = tmp_path / "clamd.sock"
        daemon = FakeClamd(clamd_path, verdict_line="stream: Eicar-Test-Signature FOUND")
        infected: Settings = settings.model_copy(
            update={
                "clamd_unix_path": str(clamd_path),
                "storage_root": tmp_path / "eicar-store",
            }
        )
        store = Path(infected.storage_root)
        store.mkdir(parents=True, exist_ok=True)
        application = create_app(infected, start_worker=False)
        try:
            probe = build_eicar_pdf(tmp_path / "eicar-probe.pdf")
            assert EICAR.encode("latin-1") in probe.read_bytes()

            user = _register(application, "eicar@example.com", "Passw0rd!23")
            response = user.post(
                "/api/v1/documents",
                **upload_kwargs(probe.read_bytes(), probe.name, PDF_MIME),
            )

            assert response.status_code == 422, response.text
            body = response.json()
            assert body["code"] == "file_rejected"

            # The contract the staging script depends on.
            assert body["details"]["reason"] == "malware_detected", body
            # And the raw serialisation must contain the literal the script greps.
            assert SCRIPT_ASSERTION in json.dumps(body, separators=(",", ":")), body

            # Nothing may be left behind by a rejected upload. Scoped to this
            # probe's filename: the test database is shared with the rest of the
            # suite, so counting every Document row asserts on other tests' data
            # and fails for reasons unrelated to this behaviour.
            from sqlalchemy import func, select

            from mafhum_api.db.models import Document

            with application.state.context.database.session() as db:
                leftovers = int(
                    db.scalar(
                        select(func.count(Document.id)).where(
                            Document.display_filename == probe.name
                        )
                    )
                    or 0
                )
            assert leftovers == 0, "rejected upload left a Document row behind"
            stored = list(store.rglob("*"))
            assert not [p for p in stored if p.is_file()], "orphaned blob left behind"
        finally:
            daemon.stop()
            application.state.context.database.engine.dispose()

    def test_an_unscannable_upload_reports_a_distinct_reason(
        self, settings, tmp_path: Path
    ) -> None:
        """Scan-required-but-unavailable must NOT read as detection.

        The staging script treats ``reason=scan_*`` as a failure, and this is why:
        conflating "the engine could not answer" with "the engine found malware"
        would make an outage look like a working control.
        """
        clamd_path = tmp_path / "absent.sock"  # nothing listening
        required: Settings = settings.model_copy(
            update={
                "clamd_unix_path": str(clamd_path),
                "malware_scan_required": True,
                "storage_root": tmp_path / "unavail-store",
            }
        )
        Path(required.storage_root).mkdir(parents=True, exist_ok=True)
        application = create_app(required, start_worker=False)
        try:
            probe = build_eicar_pdf(tmp_path / "probe2.pdf")
            user = _register(application, "unavail@example.com", "Passw0rd!23")
            response = user.post(
                "/api/v1/documents",
                **upload_kwargs(probe.read_bytes(), probe.name, PDF_MIME),
            )

            assert response.status_code == 422, response.text
            reason = response.json()["details"]["reason"]
            assert reason.startswith("scan_"), reason
            assert reason != "malware_detected"
        finally:
            application.state.context.database.engine.dispose()

    def test_a_clean_upload_is_accepted(self, settings, tmp_path: Path) -> None:
        """The control: the same probe without a FOUND verdict must be stored.

        Without this, "the scanner rejected the EICAR file" could be explained by
        the upload path rejecting PDFs generally.
        """
        clamd_path = tmp_path / "clean.sock"
        daemon = FakeClamd(clamd_path, verdict_line="stream: OK")
        clean: Settings = settings.model_copy(
            update={
                "clamd_unix_path": str(clamd_path),
                "storage_root": tmp_path / "clean-store",
            }
        )
        Path(clean.storage_root).mkdir(parents=True, exist_ok=True)
        application = create_app(clean, start_worker=False)
        try:
            probe = build_eicar_pdf(tmp_path / "clean-probe.pdf")
            user = _register(application, "clean@example.com", "Passw0rd!23")
            response = user.post(
                "/api/v1/documents",
                **upload_kwargs(probe.read_bytes(), probe.name, PDF_MIME),
            )
            assert response.status_code in (200, 201), response.text
        finally:
            daemon.stop()
            application.state.context.database.engine.dispose()
