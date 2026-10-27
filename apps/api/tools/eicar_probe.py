"""Build a structurally valid DOCX that carries the EICAR test string.

Why this module exists
----------------------
MAFHUM validates an upload's extension, MIME type and magic bytes **before** it
scans the stored bytes. So uploading a bare ``eicar.com`` declared as a document
is rejected by validation and never reaches the scanner.

A staging check that asserted only "the API returned 422" would therefore report a
passing scan on an engine that was never consulted — a false positive that makes
the single most important security control look verified when it is not.

Why a DOCX, and not a PDF
-------------------------
Live evidence from the acceptance runs on 2026-10-25 (GitHub jobs 121322493802 on
c72ff67 and the ea712e8 run) settled the vehicle:

* The daemon detected a bare EICAR file (FOUND) — the engine and signatures work.
* The same daemon returned CLEAN for a valid PDF with the canonical EICAR string
  verbatim in an uncompressed content stream, byte-verified before upload.
* It ALSO returned CLEAN for the same PDF with the raw EICAR string appended
  after ``%%EOF``, outside any PDF structure.

That is documented ClamAV behaviour, not a defect: by design ClamAV does not
match EICAR embedded or appended inside other files, because that would cause
false positives; it detects EICAR as the file itself, or **inside an archive
type it unwraps, such as ZIP** (Cisco-Talos/clamav#1277).

A DOCX *is* a ZIP archive — one of MAFHUM's two accepted upload formats — so the
probe is a real, parseable DOCX (python-docx opens it; every structural
validator passes) that additionally carries the EICAR string as a stored, i.e.
uncompressed, archive member. The engine unwraps the archive and sees the exact
contiguous bytes it is proven to flag.

The acceptance gate clamdscans the generated artifact and requires a FOUND
verdict before the API-level upload, so a probe the engine cannot see can never
again produce an ambiguous result.

This is shared by ``tools/staging_verification.sh`` and the test suite so the probe
used in staging is exactly the probe under test, and neither can drift.

EICAR is the published, non-malicious antivirus test string. Nothing here handles
real malware.
"""

from __future__ import annotations

import sys
import zipfile
from pathlib import Path

EICAR = "X5O!P%@AP[4\\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"

#: Member that carries the payload inside the container. Stored uncompressed so
#: the string is present verbatim in the archive, which is the form the engine
#: is documented to detect when it unwraps the ZIP.
PAYLOAD_MEMBER = "eicar.txt"


def build_eicar_docx(destination: Path, *, payload: str = EICAR) -> Path:
    """Write a minimal valid one-paragraph DOCX containing ``payload``.

    The result is a genuine Word document: ``PK`` magic bytes, the required
    ``[Content_Types].xml`` and ``word/document.xml`` parts, parseable by
    python-docx. ``payload`` is added as a stored archive member, so the file
    passes every structural validator while the engine, unwrapping the archive,
    scans the payload bytes themselves.

    Built with the standard library alone (zipfile + literal OOXML parts) so the
    probe needs nothing installed on the host that generates it -- CI runners do
    not have the application's dependencies. A test pins parseability by
    python-docx so the hand-built parts cannot silently stop being a document.
    """
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType='
        '"application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" ContentType='
        '"application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        "</Types>"
    )
    root_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type='
        '"http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument"'
        ' Target="word/document.xml"/>'
        "</Relationships>"
    )
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        "<w:body><w:p><w:r><w:t>MAFHUM EICAR acceptance probe</w:t></w:r></w:p></w:body>"
        "</w:document>"
    )

    with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("_rels/.rels", root_rels)
        archive.writestr("word/document.xml", document)
        member = zipfile.ZipInfo(PAYLOAD_MEMBER, date_time=(2026, 1, 1, 0, 0, 0))
        member.compress_type = zipfile.ZIP_STORED
        archive.writestr(member, payload.encode("latin-1"))
    return destination


if __name__ == "__main__":  # pragma: no cover - CLI entry point for the shell script
    if len(sys.argv) != 2:
        print("usage: eicar_probe.py <output.docx>", file=sys.stderr)
        raise SystemExit(2)
    path = build_eicar_docx(Path(sys.argv[1]))
    print(path)
