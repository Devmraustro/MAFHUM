"""Test fixture content and builders.

All text here is synthetic. It contains no real personal information, no real
student records and no real citations. Names, institutions and identifiers are
invented for testing.
"""

from __future__ import annotations

import io
import zipfile

from docx import Document as DocxDocument
from docx.enum.text import WD_BREAK

from tests.factories.pdf_factory import build_pdf

# --- synthetic Arabic content ------------------------------------------------
ARABIC_TITLE = "مدخل إلى تحليل الخوارزميات"
ARABIC_PARAGRAPHS: tuple[str, ...] = (
    "يهدف هذا الدرس إلى تعريف الطالب بمفهوم تحليل الخوارزميات وأهميته في بناء برامج فعّالة.",
    "نقيس كفاءة الخوارزمية عادةً بدلالة الزمن والذاكرة، ونعبّر عن ذلك باستعمال التدوين المقارب.",
    "يُعدّ الترتيب بالإدراج مثالًا بسيطًا، إذ تبلغ كلفته في الحالة الأسوأ مربّع عدد العناصر.",
    "أما الترتيب بالدمج فيحافظ على كلفة خطية لوغاريتمية في جميع الحالات، وهو ترتيب مستقر.",
    "خلاصة: اختيار البنية الملائمة للبيانات يقلّل الكلفة أكثر مما يفعل تحسين الشيفرة.",
)
ARABIC_DIACRITIC_TEXT = "بِسْمِ اللَّهِ الرَّحْمَٰنِ الرَّحِيمِ — نصٌّ مشكولٌ للاختبار."

# --- synthetic French content ------------------------------------------------
FRENCH_TITLE = "Introduction à l'analyse des algorithmes"
FRENCH_PARAGRAPHS: tuple[str, ...] = (
    "Ce document synthétique présente les notions de base de la complexité algorithmique.",
    "La complexité temporelle mesure le nombre d'opérations élémentaires exécutées.",
    "La notation asymptotique permet de comparer deux algorithmes indépendamment du matériel.",
    "Le tri par fusion conserve une complexité linéaire logarithmique dans tous les cas.",
    "Remarque : ce fichier est généré automatiquement pour les tests, sans données réelles.",
)


def arabic_pdf_pages(page_count: int = 2) -> list[list[str]]:
    """Multi-page Arabic content, one page's worth of paragraphs per page."""
    pages: list[list[str]] = []
    for index in range(page_count):
        lines = [ARABIC_TITLE if index == 0 else f"الصفحة {index + 1}"]
        lines.extend(ARABIC_PARAGRAPHS)
        if index == 0:
            lines.append(ARABIC_DIACRITIC_TEXT)
        pages.append(lines)
    return pages


def french_pdf_pages(page_count: int = 2) -> list[list[str]]:
    pages: list[list[str]] = []
    for index in range(page_count):
        lines = [FRENCH_TITLE if index == 0 else f"Page {index + 1}"]
        lines.extend(FRENCH_PARAGRAPHS)
        pages.append(lines)
    return pages


def make_pdf(kind: str = "arabic", *, pages: int = 2) -> bytes:
    if kind == "arabic":
        return build_pdf(arabic_pdf_pages(pages))
    if kind == "french":
        return build_pdf(french_pdf_pages(pages))
    if kind == "mixed":
        return build_pdf(arabic_pdf_pages(1) + french_pdf_pages(1))
    raise ValueError(f"unknown pdf kind: {kind}")


def make_docx(kind: str = "arabic", *, page_breaks: int = 1) -> bytes:
    """Build a DOCX with real paragraph structure and explicit page breaks."""
    document = DocxDocument()
    if kind in {"arabic", "mixed"}:
        document.add_heading(ARABIC_TITLE, level=1)
        for paragraph in ARABIC_PARAGRAPHS:
            document.add_paragraph(paragraph)
        document.add_paragraph(ARABIC_DIACRITIC_TEXT)
    if kind in {"french", "mixed"}:
        if kind == "mixed":
            document.add_paragraph().add_run().add_break(WD_BREAK.PAGE)
        document.add_heading(FRENCH_TITLE, level=1)
        for paragraph in FRENCH_PARAGRAPHS:
            document.add_paragraph(paragraph)
    for _ in range(max(page_breaks - 1, 0)):
        document.add_paragraph().add_run().add_break(WD_BREAK.PAGE)
        document.add_paragraph("فقرة إضافية بعد فاصل الصفحة / Paragraphe après le saut de page.")
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def make_table_docx() -> bytes:
    document = DocxDocument()
    document.add_heading("جدول المقارنة / Tableau comparatif", level=1)
    table = document.add_table(rows=3, cols=2)
    rows = [
        ("الخوارزمية / Algorithme", "الكلفة / Complexité"),
        ("الترتيب بالإدراج", "O(n²)"),
        ("الترتيب بالدمج", "O(n log n)"),
    ]
    for row_index, (first, second) in enumerate(rows):
        table.rows[row_index].cells[0].text = first
        table.rows[row_index].cells[1].text = second
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def make_docx_with_macro_part() -> bytes:
    """A DOCX that also carries a fake macro part (never opened by the parser)."""
    base = make_docx("arabic")
    source = zipfile.ZipFile(io.BytesIO(base))
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as target:
        for item in source.infolist():
            target.writestr(item, source.read(item.filename))
        target.writestr("word/vbaProject.bin", b"not-a-real-macro-container")
    return out.getvalue()


def make_scanned_like_pdf() -> bytes:
    """A PDF with images but no text layer — used to exercise the scanned path."""
    # 1x1 PNG, base85-free: embed raw via zlib stream.
    import zlib

    raw_png = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00"
        b"\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
        b"/Resources << /XObject << /Im0 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Subtype /Image /Width 1 /Height 1 /ColorSpace /DeviceRGB "
        b"/BitsPerComponent 8 /Filter /FlateDecode /Length "
        + str(len(zlib.compress(b"\x00\x00\x00\x00"))).encode()
        + b" >>\nstream\n"
        + zlib.compress(b"\x00\x00\x00\x00")
        + b"\nendstream",
        b"<< /Length 0 >>\nstream\n\nendstream",
    ]
    out = bytearray(b"%PDF-1.7\n")
    offsets = []
    for number, payload in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + payload + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    )
    _ = raw_png
    return bytes(out)


def make_corrupt_pdf() -> bytes:
    """Starts like a PDF but is truncated garbage after the header."""
    return b"%PDF-1.7\n" + b"\x00\xff\xfe truncated garbage " * 40 + b"\n%%EOF\n"


def make_wrong_content_pdf() -> bytes:
    """A PNG masquerading under a .pdf name — signature mismatch."""
    return (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89" + b"\x00" * 64
    )


def make_zip_bomb() -> bytes:
    """A ZIP whose declared uncompressed size is absurd — rejected pre-decompression."""
    out = io.BytesIO()
    filler = b"\x00" * (1024 * 1024)  # compresses to almost nothing
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        archive.writestr("[Content_Types].xml", _CONTENT_TYPES)
        archive.writestr("word/document.xml", _MINIMAL_DOC_XML)
        for index in range(40):
            archive.writestr(f"word/padding{index}.bin", filler)
    return out.getvalue()


def make_many_entry_zip(entry_count: int = 500) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", _CONTENT_TYPES)
        archive.writestr("word/document.xml", _MINIMAL_DOC_XML)
        for index in range(entry_count):
            archive.writestr(f"word/media/img{index}.bin", b"x" * 16)
    return out.getvalue()


def make_traversal_zip() -> bytes:
    """A ZIP with a traversal member name — must be rejected before parsing."""
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", _CONTENT_TYPES)
        archive.writestr("word/document.xml", _MINIMAL_DOC_XML)
        archive.writestr("../../etc/evil.txt", "pwned")
    return out.getvalue()


def make_not_a_docx_zip() -> bytes:
    """A valid ZIP that is not a DOCX container."""
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("readme.txt", "this is not a docx")
    return out.getvalue()


def make_corrupt_docx() -> bytes:
    return b"PK\x03\x04" + b"not a real zip payload " * 20


_CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/word/document.xml"
          ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
</Types>"""

_MINIMAL_DOC_XML = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
<w:body><w:p><w:r><w:t>نص تجريبي</w:t></w:r></w:p></w:body>
</w:document>"""
