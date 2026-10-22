"""Synthetic PDF fixture builder.

Writes small, valid PDFs with a real embedded TrueType font (a subset of the
system's DejaVu Sans) using ``Identity-H`` CID encoding plus a ``ToUnicode``
CMap. That means the produced files:

* extract correct Arabic **and** French text through a standard parser,
* render correctly in a viewer,
* stay a few kilobytes because the font is subset to the characters used,
* contain only synthetic text — no personal information.

These are test fixtures, not a general-purpose PDF library.
"""

from __future__ import annotations

import logging
import zlib
from pathlib import Path

from fontTools.subset import Subsetter
from fontTools.ttLib import TTFont

# fontTools logs dropped optional tables (FFTM etc.) at WARNING; irrelevant here.
logging.getLogger("fontTools.subset").setLevel(logging.ERROR)

DEJAVU_SANS = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")
PAGE_WIDTH = 595  # A4 points
PAGE_HEIGHT = 842
FONT_SIZE = 12
LEADING = 20
LEFT_MARGIN = 56
TOP_MARGIN = 780


class PdfBuildError(RuntimeError):
    pass


def _subset_font(text: str) -> tuple[bytes, dict[int, int], dict[int, int], int]:
    """Return (ttf_bytes, unicode->gid, unicode->width1000, units_per_em)."""
    if not DEJAVU_SANS.is_file():
        raise PdfBuildError("DejaVuSans.ttf not available for fixture generation")
    font = TTFont(str(DEJAVU_SANS))
    subsetter = Subsetter()
    subsetter.populate(text=text)
    subsetter.subset(font)

    units_per_em = int(font["head"].unitsPerEm)
    hmtx = font["hmtx"]
    widths: dict[int, int] = {}
    gid_by_codepoint: dict[int, int] = {}
    for codepoint, glyph_name in font.getBestCmap().items():
        # Identity-H codes ARE glyph IDs, so resolve names to numeric GIDs.
        gid_by_codepoint[codepoint] = font.getGlyphID(glyph_name)
        advance = hmtx[glyph_name][0]
        widths[codepoint] = round(advance * 1000 / units_per_em)

    buf = InMemoryBuffer()
    font.save(buf)
    return buf.getvalue(), gid_by_codepoint, widths, units_per_em


class InMemoryBuffer:
    """Tiny shim so ``TTFont.save`` can write into memory."""

    def __init__(self) -> None:
        import io

        self._buf = io.BytesIO()

    def write(self, data: bytes) -> int:
        return self._buf.write(data)

    def getvalue(self) -> bytes:
        return self._buf.getvalue()


def _tounicode_cmap(mapping: dict[int, str]) -> bytes:
    """Build a ToUnicode CMap from ``gid -> unicode text``."""
    lines = [
        "/CIDInit /ProcSet findresource begin",
        "12 dict begin",
        "begincmap",
        "/CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def",
        "/CMapName /Adobe-Identity-UCS def",
        "/CMapType 2 def",
        "1 begincodespacerange",
        "<0000> <FFFF>",
        "endcodespacerange",
    ]
    items = sorted(mapping.items())
    for start in range(0, len(items), 100):
        chunk = items[start : start + 100]
        lines.append(f"{len(chunk)} beginbfchar")
        for gid, unicode_text in chunk:
            encoded = "".join(f"{ord(ch):04X}" for ch in unicode_text)
            lines.append(f"<{gid:04X}> <{encoded}>")
        lines.append("endbfchar")
    lines += [
        "endcmap",
        "CMapName currentdict /CMap defineresource pop",
        "end",
        "end",
    ]
    return "\n".join(lines).encode("latin-1")


def _escape(text: str) -> str:
    return text.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")


def _rtl_runs(line: str) -> list[tuple[bool, str]]:
    """Split a line into right-to-left and left-to-right runs."""
    runs: list[tuple[bool, str]] = []
    current = ""
    current_is_rtl: bool | None = None
    for ch in line:
        is_rtl = (
            "\u0600" <= ch <= "\u06ff"
            or "\u0750" <= ch <= "\u077f"
            or ch in "\u060c\u061b\u061f\u0640\u066a"
        )
        if current_is_rtl is None:
            current_is_rtl = is_rtl
        if is_rtl != current_is_rtl:
            runs.append((current_is_rtl, current))
            current = ""
            current_is_rtl = is_rtl
        current += ch
    runs.append((bool(current_is_rtl), current))
    return runs


def to_visual_order(line: str) -> str:
    """Reorder logical text into the visual order a PDF content stream stores.

    Real Arabic PDFs store glyphs in visual (display) order; extractors apply the
    bidi algorithm to recover logical order. This mirrors that, so text written
    through :func:`build_pdf` round-trips back to the caller's original string.

    Known limitation (documented, not hidden): a line mixing Arabic with a Latin
    run does **not** reliably round-trip, because bidi run placement for mixed
    lines is ambiguous and extractors disagree. Fixtures therefore keep Arabic
    and Latin paragraphs on separate lines, which is also how real documents are
    usually laid out.
    """
    parts: list[str] = []
    for is_rtl, text in reversed(_rtl_runs(line)):
        parts.append(text[::-1] if is_rtl else text)
    return "".join(parts)


def build_pdf(
    pages: list[list[str]],
    *,
    compress_streams: bool = True,
    apply_bidi: bool = True,
) -> bytes:
    """Build a PDF whose pages contain the given lines.

    ``pages`` is a list of pages; each page is a list of text lines. Lines are
    given in logical order and reordered to visual order internally; pass
    ``apply_bidi=False`` to emit the bytes exactly as supplied.
    """
    if apply_bidi:
        pages = [[to_visual_order(line) for line in page] for page in pages]
    all_text = "".join(line for page in pages for line in page)
    if not all_text.strip():
        all_text = " "
    ttf_bytes, cmap, widths, units_per_em = _subset_font(all_text)

    gid_by_codepoint: dict[int, int] = cmap
    tounicode_map: dict[int, str] = {}

    def encode(line: str) -> str:
        hex_chars: list[str] = []
        for ch in line:
            gid = gid_by_codepoint.get(ord(ch))
            if gid is None:
                gid = gid_by_codepoint.get(0x20, 3)  # fall back to space
                tounicode_map[gid] = " "
            else:
                tounicode_map[gid] = ch
            hex_chars.append(f"{gid:04X}")
        return "".join(hex_chars)

    # --- content streams -----------------------------------------------------
    content_streams: list[bytes] = []
    for page_lines in pages:
        parts = [
            "BT",
            f"/F1 {FONT_SIZE} Tf",
            f"{LEADING} TL",
            f"1 0 0 1 {LEFT_MARGIN} {TOP_MARGIN} Tm",
        ]
        for line in page_lines:
            if not line:
                parts.append("T*")
                continue
            parts.append(f"<{encode(line)}> Tj")
            parts.append("T*")
        parts.append("ET")
        content_streams.append("\n".join(parts).encode("latin-1"))

    # --- object assembly -----------------------------------------------------
    objects: list[bytes] = []

    def add(payload: bytes) -> int:
        objects.append(payload)
        return len(objects)  # 1-based object number

    page_count = len(pages)
    # Reserve object numbers up-front so references resolve.
    kids_start = 3
    content_start = kids_start + page_count
    font_obj = content_start + page_count
    cidfont_obj = font_obj + 1
    descriptor_obj = font_obj + 2
    tounicode_obj = font_obj + 3
    fontfile_obj = font_obj + 4

    kids = " ".join(f"{kids_start + i} 0 R" for i in range(page_count))
    add(b"<< /Type /Catalog /Pages 2 0 R >>")
    add(f"<< /Type /Pages /Kids [{kids}] /Count {page_count} >>".encode("latin-1"))

    for index in range(page_count):
        page_num = kids_start + index
        content_num = content_start + index
        objects.append(
            (
                f"<< /Type /Page /Parent 2 0 R "
                f"/MediaBox [0 0 {PAGE_WIDTH} {PAGE_HEIGHT}] "
                f"/Resources << /Font << /F1 {font_obj} 0 R >> >> "
                f"/Contents {content_num} 0 R >>"
            ).encode("latin-1")
        )
        assert page_num == len(objects)

    for stream in content_streams:
        objects.append(_stream_object(stream, compress=compress_streams))

    # Widths array for the CIDFont.
    w_entries = " ".join(
        f"{gid_by_codepoint[cp]} [{widths.get(cp, 600)}]"
        for cp in sorted(gid_by_codepoint)
        if cp in widths
    )
    objects.append(
        (
            f"<< /Type /Font /Subtype /Type0 /BaseFont /DejaVuSans "
            f"/Encoding /Identity-H /DescendantFonts [{cidfont_obj} 0 R] "
            f"/ToUnicode {tounicode_obj} 0 R >>"
        ).encode("latin-1")
    )
    objects.append(
        (
            f"<< /Type /Font /Subtype /CIDFontType2 /BaseFont /DejaVuSans "
            f"/CIDSystemInfo << /Registry (Adobe) /Ordering (Identity) /Supplement 0 >> "
            f"/FontDescriptor {descriptor_obj} 0 R /DW 600 /W [{w_entries}] "
            f"/CIDToGIDMap /Identity >>"
        ).encode("latin-1")
    )
    objects.append(
        (
            f"<< /Type /FontDescriptor /FontName /DejaVuSans /Flags 4 "
            f"/FontBBox [-2000 -800 3000 2000] /ItalicAngle 0 /Ascent 1900 "
            f"/Descent -500 /CapHeight 1493 /StemV 120 "
            f"/FontFile2 {fontfile_obj} 0 R >>"
        ).encode("latin-1")
    )
    objects.append(_stream_object(_tounicode_cmap(tounicode_map), compress=compress_streams))
    objects.append(_stream_object(ttf_bytes, compress=False, extra=f"/Length1 {len(ttf_bytes)}"))

    # --- serialize -----------------------------------------------------------
    out = bytearray(b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n")
    offsets: list[int] = []
    for number, payload in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode("latin-1")
        out += payload
        out += b"\nendobj\n"
    xref_offset = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode("latin-1")
    out += b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode("latin-1")
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_offset}\n%%EOF\n"
    ).encode("latin-1")
    _ = units_per_em
    _ = _escape
    return bytes(out)


def _stream_object(data: bytes, *, compress: bool, extra: str = "") -> bytes:
    if compress:
        payload = zlib.compress(data, 9)
        header = f"<< /Length {len(payload)} /Filter /FlateDecode {extra}>>"
    else:
        payload = data
        header = f"<< /Length {len(payload)} {extra}>>"
    return header.encode("latin-1") + b"\nstream\n" + payload + b"\nendstream"


def build_single_page_pdf(lines: list[str]) -> bytes:
    return build_pdf([lines])
