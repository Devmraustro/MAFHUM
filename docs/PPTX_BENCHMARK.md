# Editable PPTX benchmark — Arabic / French, six slides

Date: 2026-10-27 · Tool: `apps/api/tools/benchmark_pptx.py`
Fixture: `apps/slides/fixtures/benchmark_deck.json` (synthetic text, no real
publication, author, DOI or legal provision is referenced)

Reproduce:

```bash
cd apps/slides && npm ci && npm test && cd ../..
PYTHONPATH=apps/api/src .venv/bin/python apps/api/tools/benchmark_pptx.py
```

Both generators receive **the same** `DeckSpec` JSON, so any difference is
attributable to the library. Outputs are written to the gitignored
`artifacts/pptx-benchmark/`.

## The deck

Six slides: title, plan (bullets), two verbatim quotation slides (one Arabic,
one French with Latin/Arabic mixing), a real column chart of citation counts, and
a real four-column reference table with footers and a draft notice. Two
synthetic citations, both explicitly marked as available.

## Measured results (this run)

| Measure | python-pptx 1.0.2 | PptxGenJS 4.0.1 |
|---|---|---|
| Generation time | **0.035 s** | 0.101 s |
| File size | **43,027 B** | 133,012 B |
| Slides | 6 / 6 | 6 / 6 |
| Native text shapes (`<p:sp>`) | 28 | 30 |
| Chart parts | 1 | 1 |
| Table parts | 1 | 1 |
| Paragraphs with `a:pPr@rtl="1"` | 37 | 33 |
| Table-level `a:tblPr@rtl="1"` | **1** | **0** |
| Presentation-level `rtl="1"` | 0 | 1 |
| `a:cs` complex-script runs | 37 | 49 |
| Arabic text preserved | yes | yes |
| French phrase preserved | yes | yes |
| ZIP integrity (`testzip`) | OK | OK |
| Opens with python-pptx (native editable shapes) | yes | yes |
| Validation issues | none | none |

Exit code of the benchmark tool: **0**. Node test suite: **2/2 passed**.
Backend presentation tests: **12/12 passed**. Generation times are single-run
measurements on a 2-CPU sandbox and vary by a few hundredths of a second; all
other numbers are deterministic properties of the generated files.

The comparison package pins `image-size@2.0.4` over PptxGenJS's `^1.2.1` range
to clear two inherited DoS advisories. That override is dev-only, audited in CI,
and does not change the generated deck (identical byte size and inspection
results before and after).

## What the numbers mean

- **Both libraries produce genuinely editable decks.** Neither is a slide-sized
  image: text boxes, a chart part and a table part all exist as native OOXML.
- **RTL is per paragraph, not a magic presentation switch.** PptxGenJS writes
  `p:presentation@rtl="1"`; python-pptx does not. The text direction that a
  renderer applies to a paragraph is `a:pPr@rtl`, which both write. We
  additionally set `a:tblPr@rtl` in the Python generator because PptxGenJS
  leaves `<a:tblPr/>` empty — that is the one measurable RTL advantage of the
  production path.
- **Mixed Arabic/French text survives in logical order** in both outputs. No
  bidi control characters were found in either deck. We do not reverse strings.
- PptxGenJS decks are ~3× larger here, mostly because of its packaged chart
  workbook and theme parts. Size is not a quality signal, but it matters for the
  output cap.

## What this benchmark does NOT prove

- **No pixel-level render was inspected.** Neither LibreOffice nor PowerPoint is
  installed in this sandbox (`command -v libreoffice soffice` → absent). A human
  must open both `.pptx` files in a presentation application and confirm Arabic
  shaping, bullet hanging, chart labels and table column order before any claim
  about visual fidelity.
- The citations are synthetic by design. No real publisher, law or page number
  was used, so this is not evidence about retrieval quality.
- Fonts are not embedded. A machine without an Arabic-capable Arial fallback may
  substitute a different face; the deck stays editable but can look different.
- No macro, animation or transition behaviour was tested; MAFHUM produces plain
  editable slides only.

## Decision

Production uses **python-pptx**; PptxGenJS remains the comparison path. The
reasoning, licensing review and deployment constraints are in
[`docs/ADR/0005-pptx-generator.md`](ADR/0005-pptx-generator.md).
