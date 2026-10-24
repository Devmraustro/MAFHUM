"""Build a structurally valid PDF that carries the EICAR test string.

Why this module exists
----------------------
MAFHUM validates an upload's extension, MIME type and magic bytes **before** it
scans the stored bytes. So uploading a bare ``eicar.com`` declared as
``application/pdf`` is rejected by validation and never reaches the scanner.

A staging check that asserted only "the API returned 422" would therefore report a
passing scan on an engine that was never consulted — a false positive that makes
the single most important security control look verified when it is not.

The fix is to probe with a file that passes structural validation and can only be
rejected by the scanner: a real, parseable PDF with the EICAR string embedded in
its page content. ClamAV matches EICAR anywhere in a file's bytes, so detection
still works, while the rejection can now only have come from the scanner.

This is shared by ``tools/staging_verification.sh`` and the test suite so the probe
used in staging is exactly the probe under test, and neither can drift.

EICAR is the published, non-malicious antivirus test string. Nothing here handles
real malware.
"""

from __future__ import annotations

import sys
from pathlib import Path

EICAR = "X5O!P%@AP[4\\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"


def build_eicar_pdf(destination: Path, *, payload: str = EICAR) -> Path:
    """Write a minimal valid single-page PDF containing ``payload``.

    The result starts with ``%PDF-``, has a well-formed xref table and one page
    whose content stream carries the payload, so it satisfies magic-byte, page
    count and parser checks.
    """
    body = f"BT /F1 12 Tf 72 720 Td ({payload}) Tj ET".encode("latin-1")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
            b"/Resources << /Font << /F1 5 0 R >> >> >>"
        ),
        b"<< /Length " + str(len(body)).encode() + b" >>\nstream\n" + body + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]

    out = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for index, obj in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{index} 0 obj\n".encode() + obj + b"\nendobj\n"

    xref_offset = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_offset}\n%%EOF\n"
    ).encode()

    destination.write_bytes(bytes(out))
    return destination


if __name__ == "__main__":  # pragma: no cover - CLI entry point for the shell script
    if len(sys.argv) != 2:
        print("usage: eicar_probe.py <output.pdf>", file=sys.stderr)
        raise SystemExit(2)
    path = build_eicar_pdf(Path(sys.argv[1]))
    print(path)
