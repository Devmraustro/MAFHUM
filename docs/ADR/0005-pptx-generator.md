# ADR 0005: python-pptx for production decks, PptxGenJS as a measured comparison

- Status: Accepted
- Date: 2026-10-27

## Context

Phase 6 needs **editable** PPTX output with real Arabic RTL and French
mixed-script support. The original architecture named PptxGenJS (MIT) as the
candidate, asked for a comparison with Marp, and required an actual Arabic/French
benchmark deck with render/editability validation.

Before choosing, three facts were established:

1. The API is a Python process built into a `python:3.11-slim` image that Gate B
   has already verified (build, non-root runtime, readiness, graceful shutdown).
   There is no Node runtime in that image.
2. Adding Node to the image would mean a second language runtime, a second
   dependency tree and an **unverified** container change: no Docker runtime is
   available in this environment, so it could not be built or tested here.
3. Both candidate libraries produce native editable objects, so this is a
   runtime/deployment decision as much as a rendering one.

## Decision

- **Production generator: `python-pptx` 1.0.2 (MIT)**, behind a `DeckGenerator`
  protocol. It writes native text boxes, a real chart (`ppt/charts/chart1.xml`)
  and a real table (`<a:tbl>`).
- **PptxGenJS 4.0.1 (MIT) is retained as a comparison generator** in
  `apps/slides`, consuming the exact same checked-in `DeckSpec` fixture. Its
  tests and `apps/api/tools/benchmark_pptx.py` run in CI on a Node runner.
- **Marp CLI is rejected** for this use case: its standard PPTX export is one
  pre-rendered image per slide (not editable), and the experimental
  `--pptx-editable` path requires both a browser and LibreOffice Impress, is
  documented as incomplete, and drops presenter notes
  ([marp-cli README](https://github.com/marp-team/marp-cli),
  [issue #166](https://github.com/marp-team/marp-cli/issues/166)). This was a
  documentation review, **not** an executed benchmark: Marp was not installed.

Generation is **synchronous inside the request**, not a background worker. With
a pure-Python generator and a structurally bounded spec (caps on slides, text,
citations and output bytes) the measured generation time is 0.035 s for the
six-slide benchmark deck. A queue would add a broker/process before any scaling
need is measured. The `DeckGenerator` interface and the `presentation_jobs` row
keep the seam open for a worker later. This deviation from the architecture's
"bounded background jobs" preference is deliberate and recorded here.

## RTL implementation

Arabic direction is written where PowerPoint actually reads it:

- `a:pPr@rtl="1"` and `algn="r"` on every paragraph;
- `a:rPr@lang="ar-DZ"` (or `fr-FR`) and an `a:cs`/`a:ea` complex-script
  typeface on every run — `a:latin` alone does not cover Arabic glyph shaping;
- `a:tblPr@rtl="1"` on tables, because table column direction is independent
  of cell paragraph direction.

Text stays in **logical order**; no string reversal and no bidi control
characters are injected. Mixed Arabic/French citations are left to the Unicode
bidi algorithm so they remain editable. No font is embedded; a widely available
font is requested and the renderer substitutes if missing.

## Consequences

- One language runtime in the API container; the verified Dockerfile is
  unchanged.
- Both generators are exercised in CI. The Python path is covered by 12 backend
  tests (isolation, RTL markers, chart/table parts, ZIP integrity, deletion,
  quota, failure handling); the Node path has 2 tests and the benchmark tool.
- PptxGenJS is a **dev/benchmark dependency only** and never ships.
- Measured difference: PptxGenJS writes a presentation-level `rtl="1"` and does
  **not** set `a:tblPr@rtl`; python-pptx does not write the presentation-level
  flag but does set table direction. Both pass the editable/chart/table/RTL
  gates. See [`docs/PPTX_BENCHMARK.md`](../PPTX_BENCHMARK.md).
- **Not verified:** pixel-level rendering. No LibreOffice or PowerPoint is
  installed in the sandbox, so the visual result of either deck in a real
  presentation application is unconfirmed. This is stated in the benchmark and
  must be checked manually before a release claim.

## Revisit when

- A real deployment shows generation blocking request latency, or
- A measured RTL rendering defect appears in PowerPoint/LibreOffice that one
  library handles and the other does not, or
- The API image gains a Node runtime for an independently justified reason.
