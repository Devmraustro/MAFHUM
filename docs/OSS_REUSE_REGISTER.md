# Open-source reuse register

Every candidate evaluated for Phase 3 document ingestion, with the **verified**
license of the version inspected, its dependency weight, and the reason it was
adopted or rejected.

Method: licenses were read from the upstream repository via the GitHub license
API and from PyPI metadata (`license_expression` / classifiers) for the exact
version considered — not inferred from popularity or from a package's README.

---

## Adopted

### pypdf 6.21.2 — PDF text extraction

- **License:** BSD-3-Clause (permissive; GitHub reports `NOASSERTION` only because
  the text is the non-canonical variant carrying a name-endorsement clause.
  Copyright 2006–2008 Mathieu Fenniak and contributors.)
- **Why adopted:** pure Python, no native binaries, no model downloads, actively
  maintained (three releases within the week of inspection), and it exposes
  enough structure to detect encryption and image-only pages.
- **Rejected alternative it beat:** pdfminer.six (also MIT, viable, but fewer
  dependencies is better and pypdf is more actively maintained).

### python-docx 1.2.0 — DOCX text extraction

- **License:** MIT
- **Dependencies:** only `lxml>=3.1.0` and `typing_extensions`
- **Why adopted:** minimal dependency surface, and MAFHUM reads only
  `word/document.xml` plus tables. `vbaProject.bin` is **detected but never
  opened**, so macros are never parsed or executed.

### fonttools 4.67.0 — test fixtures only

- **License:** MIT
- **Why:** builds the synthetic Arabic/French PDFs used by the test suite.
  Dev/test dependency; never shipped.

---

## Rejected, with reasons

### PyMuPDF — rejected on license and weight

- **License:** **AGPL-3.0**, with a separate commercial licence available.
- **Reason:** AGPL's network copyleft is incompatible with this project's
  licensing posture, and no explicit compatibility review has been authorised.
  It also pulls `onnxruntime`, `pymupdf-layout` and `pymupdf4llm`, which means
  model downloads at install time — a direct violation of the "no large model
  downloads in the default installation" constraint.

### Docling — rejected on dependency weight

- **License:** MIT (acceptable)
- **Reason:** the slim distribution alone declares **184 dependencies**
  (`docling-core`, `filetype`, `langcodes`, `pluggy`, `requests`, `tqdm`, …) and
  the full pipeline downloads models at runtime. Far too heavy for a
  zero-budget default install on ordinary hardware.

### OCRmyPDF — rejected on runtime requirements

- **License:** MPL-2.0 (acceptable)
- **Reason:** it is a wrapper around the `tesseract` binary plus language packs,
  neither of which can be assumed present, and which could not be installed or
  verified in the target environment. Shipping an OCR path that cannot be
  exercised would mean claiming a capability that does not work.
- **Recorded as the future OCR path**, with its true install cost:
  `tesseract-ocr` + `tesseract-ocr-ara` + `tesseract-ocr-fra` + `qpdf`.

### Apache Tika — rejected on runtime requirements

- **License:** Apache-2.0 (acceptable)
- **Reason:** requires a JVM and a running Tika server. That is a service
  dependency, not a library, and it contradicts the modular-monolith,
  ordinary-hardware constraints.

### pdfminer.six — viable, not selected

- **License:** MIT
- **Reason not selected:** pypdf covers the requirement with fewer dependencies
  and more active maintenance. Kept on record as the fallback PDF parser if
  pypdf ever regresses on Arabic extraction.

### postgresql-wheel — dead end

- **License:** Apache-2.0 (acceptable)
- **Reason:** publishes no cp311 wheel, so it cannot be installed on Python 3.11.

### pgserver 0.1.4 — accepted for tests only

- **License:** **Apache-2.0**, verified against the upstream repository
  (`github.com/orm011/pgserver`). PyPI publishes no license field, which is why
  this needed checking at source rather than trusting the registry.
- **Reason accepted:** it bundles a self-contained PostgreSQL 16.2 and is the
  only way to run real PostgreSQL integration tests in an environment with no
  system database. It is a **dev/test dependency only** and never ships.
- **Caveat:** last upstream push was mid-2024 — maintained, but not fast moving.

---

## Decision summary

| Need | Decision |
|---|---|
| PDF text extraction | pypdf 6.21.2 (BSD-3-Clause) |
| DOCX text extraction | python-docx 1.2.0 (MIT) |
| Scanned-PDF handling | **Detect and report; do not OCR** |
| Malware scanning | **Not integrated — do not claim it** |
| Test PostgreSQL | pgserver 0.1.4 (dev/test only, Apache-2.0) |
| Editable PPTX (production) | python-pptx 1.0.2 (MIT) |
| Editable PPTX (comparison only) | PptxGenJS 4.0.1 (MIT, dev/benchmark) |
| Markdown → editable PPTX | **Marp CLI rejected** (image export; editable mode experimental) |

Two overlapping PDF parsers were deliberately **not** adopted. One parser per
format keeps the extraction behaviour predictable and the audit surface small.


---

## Phase 6 — editable presentations (2026-10-27)

The upstream README, the exact LICENSE file, the package manifest, release
activity and the dependency tree were inspected for each candidate. Licenses
below were read from the upstream LICENSE text, not inferred from a README badge.

### Adopted: python-pptx 1.0.2 — production generator

- **License:** MIT — verified from
  `https://raw.githubusercontent.com/scanny/python-pptx/master/LICENSE`
  (Copyright (c) 2013 Steve Canny). PyPI metadata for 1.0.2 also reports MIT.
- **Why:** pure Python, no native binaries or model downloads, active project,
  produces native editable text/chart/table objects. It runs inside the already
  verified `python:3.11-slim` API image without adding a second runtime.
- **Dependencies pulled in:** `lxml`, `Pillow`, `XlsxWriter`, `typing_extensions`.
  `pip-audit` reports no known vulnerabilities for the pinned tree.
- **Modifications:** none to the library. RTL attributes are written through its
  public XML model (`a:pPr`, `a:rPr`, `a:tblPr`) from MAFHUM's own generator.
- **Test status:** 12 backend tests + the six-slide benchmark; see
  `docs/PPTX_BENCHMARK.md`.

### Comparison only: PptxGenJS 4.0.1 — not in the production image

- **License:** MIT — verified from
  `https://raw.githubusercontent.com/gitbrent/PptxGenJS/master/LICENSE`
  (Copyright (c) 2015-2022 Brent Ely); npm registry metadata also reports MIT.
- **Review:** actively maintained, 19 transitive packages in our install,
  built-in `rtlMode` and `lang` text options, native chart/table APIs.
- **Why not production here:** the API container has no Node runtime. Adding one
  would change an image that Gate B already verified, and Docker is unavailable
  in this environment to re-verify it. It stays as a dev/benchmark dependency in
  `apps/slides` and is pinned exactly.
- **Security review (2026-10-27):** `npm audit` on the unmodified install
  reported **2 high advisories** inherited through `image-size@1.2.1`
  (GHSA-5p2g-fcmc-qvqq and GHSA-w3rx-r6r6-pgpr — infinite-loop DoS in JXL/HEIF
  and ICNS parsers). Upstream 4.0.1 declares `image-size ^1.2.1`; 4.0.0 is not
  a clean fix either. `apps/slides/package.json` therefore pins an `overrides`
  entry to **`image-size@2.0.4` (MIT)**, which is outside both advisory ranges.
  This is a major-version override, justified by a narrow scope: the benchmark
  spec contains **no images**, so the image-size API is never called. The
  override is confined to the dev/benchmark package and is verified by
  `npm audit` (0 vulnerabilities), the Node tests and an unchanged 6-slide
  benchmark output. Remove it when upstream publishes a fixed dependency range.
- **Measured caveat:** it does not set `a:tblPr@rtl` for tables and can leave
  mixed-script runs with the default `en-US` language unless `lang` is passed
  explicitly (we pass it). See the benchmark.

### Rejected: Marp CLI — documentation review, not installed

- **License:** MIT per package metadata (previously recorded in this register).
- **Reason:** its standard PPTX export is one pre-rendered image per slide, so
  the output is **not editable** — a hard requirement. The experimental
  `--pptx-editable` mode requires both a browser and LibreOffice Impress, warns
  it may produce incomplete results, and drops presenter notes. Neither runtime
  is available here and the upstream maintainer describes web-to-editable-PPTX
  conversion as inherently unreliable. Marp was therefore not installed or
  benchmarked; this is an explicit, evidence-backed rejection, not a measured
  tie.
- **Sources:** `https://github.com/marp-team/marp-cli`, issue #166,
  discussion #82.

### Unchanged

No code from any of these projects was copied into MAFHUM. All are consumed as
declared dependencies, and `THIRD_PARTY_NOTICES.md` records the pinned versions.
