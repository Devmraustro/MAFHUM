# Phase 4 Security & Grounding Gates / Phase 5 Integration — Completion Report

> **Historical report (2026-10-23), superseded.** PR #1 merged on
> 2026-10-27 and Phases 6–7 are implemented. Git state, PR status and
> "not started" claims below describe that earlier tree only. Current
> state: `docs/STATUS_REPORT_2026-10-27.md` §15 and `docs/ROADMAP.md`.

Date: 2026-10-23 · Branch: `arena/1cb7b9d2-mafhum` · Final commit: `34a1d6d`

## 1. Verified repository state

| Item | Value | How verified |
|---|---|---|
| Starting commit | `1b90560` (SSRF gate) | `git log` |
| Final commit | `34a1d6d` | `git log` |
| Branch | `arena/1cb7b9d2-mafhum` | `git branch` |
| `origin/main` | `c4c1e3b` — unmodified, not merged | `git rev-parse origin/main` |
| PR #1 | **OPEN**, `MERGEABLE` | `gh pr view 1` |
| CI run `39947544258` | **all 4 jobs success** | `gh run view --json jobs` |
| Working tree | clean | `git status --porcelain` |

Commits added this session: `29db90a` (grounding), `dab4ebb` (research→workspace backend),
`c2b55ee` (frontend), `8bc0c98`, `9020c6e`, `cdd7157`, `34a1d6d` (CI diagnosis and fixes).

## 2. Priority 1 — SSRF security review (complete, `1b90560`)

Full detail in `docs/SSRF_REVIEW.md`. Three real defects, none caught by the 68 pre-existing tests:

1. **DNS rebinding (high) — fixed.** `fetch()` passed `target.url` to the client, so httpcore
   re-resolved; the redirect hop called `validate_url()` and discarded the result. Measured
   **2 `getaddrinfo` per fetch → 1**; `connected_to == [("dial.test", 80, "93.184.216.34")]`.
2. **Deny-by-default (medium) — fixed.** `100.64.0.0/10` (CGNAT) passed and `192.88.99.0/24`
   reported `is_global`. `is_global` is now required *and* the address must not be a named
   special-purpose range. No over-block: `8.8.8.8`, `1.1.1.1`, `93.184.216.34`,
   `2606:2800:220:1:248:1893:25c8:1946`, `2001:4860:4860::8888`, `41.106.3.114`, `193.194.66.29`.
3. **Port handling (low) — fixed.** `:0` silently became 80; `:65536` raised an unhandled
   `ValueError` (500).
4. Response body is read via `iter_bytes()` with the cap applied to the **decoded** stream.

No security check was weakened or removed. `tests/test_ssrf_review.py`: **119 tests**, and
`tests/test_research.py` **69 tests, 1 skip** (`test_joradp_is_reachable`).

**Still open — stated plainly:** live publisher retrieval is unverifiable here. DNS resolves
(`www.joradp.dz` → `41.106.3.114`), TCP :443 connects, but the TLS handshake fails
(`SSLZeroReturnError`). Retrieval is genuinely impossible, not merely untested. There is also no
end-to-end test through real ports 80/443, no adversarial rebinding server, and no cloud-metadata
probe. Source classification remains **IMPLEMENTED, FIXTURE-VERIFIED, LIVE-SOURCE-UNVERIFIED**.

## 3. Priority 2 — claim-support accuracy

The baseline dataset is **unchanged**, verified by SHA-256
(`ae55a569872deead615f35423f531cefba02a704dd05c85e093fb3d7c91aa02b`) rather than by git history,
so it holds in a shallow clone.

### Original 24 cases (12 ar / 12 fr), metric definition untouched

| Metric | Original | Now |
|---|---|---|
| status_correct | 24/24 | **20/24 (83.3%)** |
| citation_resolves | 24/24 | 24/24 |
| quote_is_verbatim | 24/24 | 24/24 |
| no_fabrication | 24/24 | 24/24 |
| **claim_support_honest** | **20/24 (83.3%)** | **24/24 (100%)** |
| not_over_refusing | — | 24/24 |

Exit code **1**, by design. The 4 deviating cases are exactly `ar-06`, `ar-07`, `fr-05`, `fr-06` —
each labelled `expected_status="grounded"` *and* `supports_claim=false`, i.e. the label recorded
the bug. **The drop in `status_correct` is the label being stale, not a regression.**

No threshold could separate them: `ar-07` scores a perfect **1.000** and still fails, while
`ar-06` (0.400) is indistinguishable from the legitimately supported `ar-08` (0.400). The fix was
made at the stage that overstates, not at the threshold:

- **Contradiction detection.** Two passages sharing a subject but stating different numeric values
  contradict each other. Detection now downgrades to `insufficient_evidence` while keeping both
  passages visible. Jaccard was the wrong measure (contradicting passages agree on the subject and
  differ on the verb → low overlap); containment against the smaller set is used instead. Arabic
  numerals carry prefixes, so `بست` ("six") matches after normalization. Conflict is checked
  against the ranked pool, not only the citations — one case had a contradiction invisible because
  the second passage never cleared `MIN_SEGMENT_SCORE`.
- **Polar questions.** Detecting question *form* is reliable in a way entailment is not; a lexical
  match cannot establish whether a yes/no answer is yes. Such questions are declined, with related
  passages still shown and labelled as related rather than proof.

### New v2 dataset — 38 cases (19 ar / 19 fr)

The same 24 cases with the 4 stale labels corrected, plus 12 new: topical similarity without
entailment, partial evidence, further contradictions, more insufficient-evidence, polar-answerable
controls, grounded controls, no-evidence.

| Metric | v2 |
|---|---|
| status_correct | 33/38 (86.8%) |
| citation_resolves | 38/38 |
| quote_is_verbatim | 38/38 |
| no_fabrication | 33/38 (86.8%) |
| **claim_support_honest** | **37/38 (97.4%)** |
| not_over_refusing | 38/38 |
| conservative_cost | 2 |

Five still-failing cases are kept in the dataset and enumerated in a test, so a new failure is
caught and a quiet deletion is impossible: `ar-13-topical-no-entailment`, `fr-14-partial-evidence`,
`fr-15-numeric-contradiction`, `ar-16-insufficient`, `fr-16-insufficient`.

**Disclosed limitation:** the evaluator is lexical. It cannot establish semantic entailment —
`ar-13` proves it, scoring highly on a passage that merely shares the topic. `fr-15`'s
contradiction is stated in words ("la majorité" vs "les deux tiers") with one numeral between them
and is still missed. Verbatim quotation, citation existence and lexical overlap are **not** treated
as semantic support.

## 4. Priority 3 — research saved into a study workspace

**Design decision, stated explicitly: both a snapshot and references.** A reference alone would make
saved material vanish when its source is deleted. A snapshot alone is worse — a quotation would keep
looking citable after its source was gone. So the text is stored *and* `source_document_ids` records
provenance, with availability **recomputed on read**, never stored as a permanent truth.

`source_document_ids` is a JSONB array rather than a foreign key on purpose: the row must survive the
document's deletion, so there is nothing to cascade.

The save payload is the shape the research endpoint already returns, so saving copies what the
student saw instead of re-running the query — a re-run could return different evidence and quietly
upgrade a weak result into study material that looks better supported than it was. `status` and
`evidence_sufficient` are stored as given, so an `insufficient_evidence` result stays labelled that
way forever.

Endpoints: `POST`/`GET /api/v1/study/topics/{id}/research`, `GET /api/v1/study/research/{saved_id}`.
Ownership is enforced inside the service; every cross-user path returns **404, never 403**, so an id
cannot confirm a row exists. Duplicate saves return the existing row flagged `deduplicated`, scoped
per topic.

Frontend: a save panel under a grounded result listing the student's own topics. An unsupported
result is not offered for saving at all. 8 new i18n keys in **both** Arabic and French, key sets
asserted identical (158 each).

A real defect was found while testing the frontend: the save button was enabled with no topic
selected, so clicking it did nothing — an inert control. `canSave` now requires a chosen topic, and
the test asserts both the disabled state and that a click fires no request.

**Tests.** `tests/test_study_research_integration.py` (21): save and retrieval, database-level
persistence read straight from the table, listing, reopen by id, save into a newly created topic,
duplicate saves, per-topic deduplication, insufficient-evidence preservation, unparseable and
unknown document ids reported missing, **source deletion flipping `sources_available` to false while
keeping the text**, cross-user read and save, oversized/empty/missing fields, rollback.
`research-save.test.tsx` (7): topic listing, payload and CSRF header, duplicate reporting, refusing
an unsupported result, the disabled control, no-topics, surfaced failure.

Migration `0005` verified **up → down → up → down → up** on PostgreSQL 16.2.

## 5. Quality gates

| Check | Result |
|---|---|
| Backend suite | **787 tests, 0 failures, 0 errors, 3 skips**, 119.1s, exit **0** |
| Concurrency + migrations on real PostgreSQL | 17 tests, 0 failures |
| `ruff check .` (CI scope, from `apps/api`) | All checks passed |
| `ruff format --check .` | 93 files already formatted |
| `mypy src` (strict) | Success, 56 files |
| `pip-audit` | No known vulnerabilities |
| Frontend typecheck / lint / tests / build | 0 / 0 / **101 tests, 8 files** / OK |
| Grounding v1 / v2 | exit 1 as designed (see §3) |
| GitHub Actions `39947544258` | **all 4 jobs success** |

Skips: `test_joradp_is_reachable` (no live egress), plus two opt-in `live_source` tests requiring
`MAFHUM_LIVE_RESEARCH=1`. No live test is reported as passing.

## 6. CI failure investigation — recorded because two of my diagnoses were wrong

CI failed on `1b90560`, `29db90a`, `c2b55ee` while every local check passed. The run logs live on a
blob host outside this environment's egress allowlist, so the fetch ends in `EOF`, leaving only
"Process completed with exit code 1".

I guessed twice and was wrong both times:

1. **Wrong:** asserted the failure was two tests demanding that TLS to a publisher must fail. That
   was a genuine bug — the tests did encode this sandbox's egress — and worth fixing, but it was not
   the cause.
2. **Wrong:** asserted five pinning tests dialing a real public address were the cause. Also a real
   defect worth fixing, also not the cause.

Both guesses were rejected by reproduction: the suite passed in my working venv **and** in a freshly
created venv installed with `pip install -e ".[dev]"` exactly as CI does. I also attempted a
simulation of working egress and it proved nothing, because validation refused the fetch before any
connection — an invalid experiment that I have not counted as evidence.

So I stopped guessing and made the runner report its own failure: on a non-zero pytest exit, the
failing test names and assertion lines are re-emitted as **workflow annotations**, which the GitHub
API does serve. The exit code is preserved exactly via `PIPESTATUS[0]`. Verified in both directions
before pushing: a failing suite exits 1 with annotations naming the test; a passing suite exits 0
with zero annotations.

The annotations gave the real evidence — three failures, none about egress:

1. The runner resolved a real publisher name to an address in `192.168.0.0/16` (NAT or filtering
   proxy). The guard correctly refused it; the test wrongly assumed a public answer. Now skips with
   the reason recorded.
2. Same cause surfacing as `SsrfError(resolves_to_blocked_address)`. This now asserts the positive
   security property — refused *because* at least one answer was non-global. A refusal of an
   all-public answer still fails the test.
3. The baseline test shelled out to `git show 0f1c654:…`, which exits **128** in CI because
   `actions/checkout@v4` clones at depth 1. Replaced with an embedded SHA-256.

Each fix was verified against the condition that produced it: a real shallow clone (`git show` exits
128) passes the digest test and skips the git cross-check; the digest test is **not vacuous** —
tampering with the baseline makes it fail.

## 7. Security and privacy findings

Fixed this session or previously: DNS-rebinding pinning gap, unbounded decompression, CGNAT and
`192.88.99.0/24` bypass, port normalization, `/openapi.json` exposure, concurrent retry/delete races,
blob-deleted-before-commit (documents and accounts), concurrent-deletion 500s.

Unchanged and disclosed: malware scanning is absent (`NullScanner.available is False`, fails closed)
— **detection UNVERIFIED**; no OCR; quotas, rate limits and worker state are **process-local**; no
encryption at rest.

## 8. Remaining gaps

**Phase 4:** live publisher retrieval unverified (TLS blocked); no real-socket SSRF test; malware
detection unverifiable (no engine or signature DB installable here); Docker/Compose and
backup/restore **UNVERIFIED** — no container runtime present.

**Phase 5:** the lexical grounding limitation in §3. Phases 6 and 7 were not started and are out of
scope.

## 9. Release recommendation

**Not recommended for production as a complete product.** The Phase 1–5 subset is suitable for
**staging only**, and never with more than one API process, because quotas, rate limits and worker
state are process-local.

Blocking items: live-source retrieval unverified; malware scanning absent; no encryption at rest;
single-process constraint; the lexical grounding limitation.

Green and verified: CI fully green across all four jobs; 787 backend tests and 101 frontend tests
passing; strict typing and lint clean; dependency audits clean; SSRF review complete with its
unverified paths named; the grounding baseline improved from 83.3% to 100% honest claim support
without redefining the metric.
