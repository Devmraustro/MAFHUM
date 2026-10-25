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
its page content, so the rejection can now only have come from the scanner.

Where the payload goes, and why in two places
---------------------------------------------
The payload is written (1) inside the page content stream, and (2) as a raw line
after the ``%%EOF`` marker. The second placement is the one that makes detection
real: live evidence from the acceptance run on 2026-10-25 (GitHub job 121322493802,
commit c72ff67) showed ClamAV returning CLEAN for the content-stream embedding —
the same daemon that detects a bare EICAR file — so the earlier assumption that
"ClamAV matches EICAR anywhere in a file's bytes" is false when the engine
recognises the container as a PDF. After ``%%EOF`` the bytes are outside any PDF
structure, which is exactly the form the engine is proven to detect, while the
file still parses as a one-page PDF (readers ignore trailing bytes).

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
from pathlib import Path

EICAR = "X5O!P%@AP[4\\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"


def build_eicar_pdf(destination: Path, *, payload: str = EICAR) -> Path:
    """Write a minimal valid single-page PDF containing ``payload``.

    The result starts with ``%PDF-``, has a well-formed xref table and one page
    whose content stream carries the payload, so it satisfies magic-byte, page
    count and parser checks. The payload is repeated as a raw line after
    ``%%EOF`` — see the module docstring for why that placement is required
    for real-engine detection.
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
    # The detectable placement: raw payload bytes outside any PDF structure.
    # (An earlier version ended the file at %%EOF and relied on the
    # content-stream embedding, which a real ClamAV demonstrably does not flag.)
    out += payload.encode("latin-1") + b"\n"

    destination.write_bytes(bytes(out))
    return destination


if __name__ == "__main__":  # pragma: no cover - CLI entry point for the shell script
    if len(sys.argv) != 2:
        print("usage: eicar_probe.py <output.pdf>", file=sys.stderr)
        raise SystemExit(2)
    path = build_eicar_pdf(Path(sys.argv[1]))
    print(path)
