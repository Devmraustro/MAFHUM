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

### pgserver 0.1.4 — accepted for tests only, with a caveat

- **License:** **not published** (no PyPI license field, no classifier)
- **Reason accepted:** it bundles a self-contained PostgreSQL 16.2 and is the
  only way to run real PostgreSQL integration tests in an environment with no
  system database. It is a **dev/test dependency only** and never ships.
- **Open item:** its licensing must be clarified before MAFHUM is ever
  redistributed as a bundle.

---

## Decision summary

| Need | Decision |
|---|---|
| PDF text extraction | pypdf 6.21.2 (BSD-3-Clause) |
| DOCX text extraction | python-docx 1.2.0 (MIT) |
| Scanned-PDF handling | **Detect and report; do not OCR** |
| Malware scanning | **Not integrated — do not claim it** |
| Test PostgreSQL | pgserver 0.1.4 (dev/test only, license unverified) |

Two overlapping PDF parsers were deliberately **not** adopted. One parser per
format keeps the extraction behaviour predictable and the audit surface small.
