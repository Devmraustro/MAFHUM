# Public landing page and navigation

The public home page (`apps/web/src/app/page.tsx` + `home-content.tsx`) is the
first thing a visitor sees, which makes it the easiest place in the product to
overstate what exists. It is written under one rule:

> Every claim on the page maps to a capability that is implemented and covered by
> a test. Limitations are stated on the page itself, not only in `README.md`.

`apps/web/src/app/__tests__/home-page.test.tsx` enforces the parts of that rule
a test can check: the primary call to action reaches the real registration route,
all four capability headings are present, the no-OCR and no-bundled-engine
sentences are rendered, every illustration has a text alternative, and no
"generates/writes content for you" phrasing appears.

## What the page says, and why it is allowed to say it

| Claim | Backed by |
|---|---|
| Upload PDF/DOCX with strict validation | `services/documents/validators.py`, `test_api_upload_validation.py` |
| Arabic + French extraction | `parsers/`, `test_unit_text.py` |
| Grounded answers with verbatim citations and page numbers | `services/research/grounding.py`, `test_grounding_*.py` |
| "Insufficient evidence" is a real answer | same, plus `docs/GROUNDING_EVALUATION.md` |
| SM-2 flashcards and study plans | `services/study/scheduling.py`, `test_study.py` |
| Editable PPTX with Arabic RTL | `services/presentations/`, `docs/PPTX_BENCHMARK.md` |
| No OCR | stated as a limitation; `ocr_available` is `false` in `/documents/limits` |
| No bundled malware engine | stated as a limitation; `/documents/limits` discloses the live state |
| Full export, permanent deletion, per-account isolation | `services/account/export.py`, `services/deletion/`, `test_api_isolation.py` |

## Route changes

`/` used to be the sign-in form. It is now the landing page, and the form moved to
`/login`. Every client redirect that pointed at `/` for "you are signed out" now
points at `/login` (both in the app and in the test that asserts it), because a
signed-out student landing on a marketing page instead of a form is worse
navigation, not better.

## Motion

All animation is CSS: a short fade-and-rise on entry. There is no scroll
observer, so nothing waits for JavaScript to become visible, and
`prefers-reduced-motion: reduce` disables every animation and transition. The
layout is identical either way.

## Imagery

Four illustrations live in `apps/web/public/landing/` as WebP (120 KB total,
resized and converted from the generated PNG masters). Each one is referenced by
a stable filename that a test asserts exists on disk, because a missing file in
`public/` fails silently (`next/image` receives a runtime string).

They are abstract on purpose: no text, no letters, no fake screenshots. A fake UI
screenshot on a landing page is a claim about an interface that may not exist.

## Verified how

- `npx tsc --noEmit`, `npx eslint . --max-warnings 0` — clean.
- `npx vitest run` — 127 tests, including the six landing-page tests.
- `npx next build` — all routes prerender; `/` is static.
- Rendered over HTTP from the dev server and checked for the Arabic headline,
  the CSS bundle and the WebP asset (200 for each).

**Not verified:** a real browser screenshot. Chromium could not be downloaded in
the build environment used for this change, so visual review (type scale,
spacing, the hero illustration crop at narrow widths) was not performed here and
should be done by opening the running app.
