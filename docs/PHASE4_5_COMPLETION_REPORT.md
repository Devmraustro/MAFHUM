# Phase 4 completion gates + Phase 5 tutoring — report

> **Historical report (2026-10-23), superseded.** PR #1 merged on
> 2026-10-27 and Phases 6–7 are implemented. Git state, PR status and
> "not started" claims below describe that earlier tree only. Current
> state: `docs/STATUS_REPORT_2026-10-27.md` §15 and `docs/ROADMAP.md`.

Date: 2026-10-23. Branch `arena/1cb7b9d2-mafhum`.

## Verified git state

| Item | Value | How verified |
| --- | --- | --- |
| Start of this session | `86b3669` | `git rev-parse HEAD` after a non-destructive `reset --mixed origin/...` |
| Final HEAD | `0cded42` | `git rev-parse HEAD` |
| `main` / `origin/main` | `c4c1e3b` — **unmodified** | `git rev-parse main origin/main` |
| PR #1 | OPEN, MERGEABLE, head `0cded42` | `gh pr view 1 --json state,mergeable,headRefOid` |
| CI | run **39871958816** on `0cded42` — **success, 4/4 jobs** | `gh run view --json jobs,conclusion` |

No force-push, no merge to `main`, no deployment.

### Commits added this session

| SHA | Subject |
| --- | --- |
| `186b8a7` | fix(api): stop concurrent account deletion raising StaleDataError |
| `4104d3c` | feat(api): deduplicate sources on a canonical URL identity |
| `0f1c654` | feat(api): add a labelled citation/grounding evaluation dataset |
| `d496e41` | fix(api): use pytest tmp_path_factory for the evaluation report |
| `3cd6b0a` | feat(api): Phase 5 tutoring backend |
| `1eaa7e3` | feat(web): Phase 5 learning workspace |
| `0cded42` | fix(api): satisfy `ruff check .` over the alembic migrations |

Phase 4 corrections and Phase 5 implementation are in separate reviewable commits.

## A real defect found and fixed

Concurrent `DELETE /api/v1/me` intermittently returned **500**. Reproduced and captured verbatim:

```
sqlalchemy.orm.exc.StaleDataError: UPDATE statement on table 'users'
expected to update 1 row(s); 0 were matched.
```

`tombstone_account` wrote the tombstone through the ORM. Two concurrent deletions
both read the row; whichever flushed after the winner removed it emitted an UPDATE
against a deleted row. Fixed with a rowcount-checked conditional Core UPDATE, and
existence re-read via `select(User.id)` — `db.get()` returns a stale identity-map
instance and reports a deleted row as present.

This is very likely the cause of the earlier CI failure on `09ad705`, whose log is
no longer retrievable.

**Two things worth recording about the test.** My first attempt constructed the
interleaving by deleting the row between a `session.get` and the tombstone write.
It **passed with the bug present** — `db.get` had already dropped the instance, so
the buggy path was never reached. I replaced it rather than keeping a test that
cannot fail. The committed test runs the four-caller service-level race across
eight fresh accounts: verified it **fails** with the bug reintroduced and passes
3/3 with the fix.

## Stage 1A — source deduplication

Identity is the **canonical URL only**. Titles, authors, DOIs and years are never
used, so two different papers with near-identical titles stay separate (tested).

Enforcement is a **partial unique index** on `(owner_id, url_normalized) WHERE
url_normalized IS NOT NULL`, not a pre-insert lookup — a lookup cannot survive two
concurrent inserts. On a clash the service returns the record that won the race.
Scoped per owner: two students cataloguing the same paper hold independent records.

Canonicalization normalizes only provable equivalence (fragment, scheme/host case,
trailing dot, default port) and leaves alone `http` vs `https`, `www.`, path case,
query strings, trailing slashes and non-default ports. Unparseable,
non-http(s), credential-bearing or over-long URLs get no identity.

Policy, known false positives and known false negatives: `docs/SOURCE_DEDUPLICATION.md`.

`tests/test_sources_dedup.py` — **25 tests**. Migration `0003_source_dedup`
verified `upgrade head → downgrade base → upgrade head`, all exit 0, column and
index confirmed present.

## Stage 1B — citation/grounding evaluation

`apps/api/tests/evaluation/citation_grounding_v1.json` — **24 cases, 12 Arabic / 12
French**. All passages synthetic; the file says so, and nothing in it refers to a
real publication, author, DOI or page number.

**Measured results** (runner exit code 0):

| Metric | Score | Rate |
| --- | --- | --- |
| status_correct | 24/24 | 100.0% |
| citation_resolves | 24/24 | 100.0% |
| quote_is_verbatim | 24/24 | 100.0% |
| no_fabrication | 24/24 | 100.0% |
| **claim_support_honest** | **20/24** | **83.3%** |

The 83.3% is the point of the exercise, and it is reported rather than fixed or
hidden. In `ar-06`/`fr-05` the question asks whether the President may dissolve
Parliament; the retrieved passage says the President *addresses* Parliament.
Subject and institution both match, so the passage is cited — but it says nothing
about dissolution. **The quotation appears verbatim in all four failing cases.** A
metric computed from presence alone would have scored 100% and been false. A
lexical scorer cannot detect non-support.

One correction to record: an earlier version of `no_fabrication` counted any
citation returned under `insufficient_evidence` as a fabrication. That was wrong.
Showing the partially relevant passages while reporting `evidence_sufficient:
false` and declining to answer is the opposite of fabricating support.

## Stage 1C — live source status: **BLOCKED**

Tested directly this session:

```
curl https://www.joradp.dz        → SSL_ERROR_SYSCALL
curl https://www.asjp.cerist.dz   → SSL_ERROR_SYSCALL
curl https://www.sndl.cerist.dz   → SSL_ERROR_SYSCALL
```

Sandbox egress is allowlisted to github.com, api.github.com, pypi.org and
registry.npmjs.org. **No live retrieval or extraction was observed on real
content, so no publisher may be called integrated.** Classification:
**IMPLEMENTED, FIXTURE-VERIFIED, LIVE-SOURCE-UNVERIFIED.**

`TestLiveSourceIntegration::test_joradp_is_reachable` is the 1 skipped test and
stays opt-in; the default suite never needs network.

## Stage 1D — SSRF review: **NOT PERFORMED THIS SESSION**

I did not carry out the DNS/rebinding/redirect-hop review asked for in 1D. The
existing protections and their 68 deterministic tests are unchanged and passing,
but I have no new evidence to add and am not claiming any. **This gate remains
open.**

## Stage 2 — Phase 5 tutoring

Seven tables (migration `0004_study`), `StudyService`, `/api/v1/study`, and the
`/study` frontend.

| Area | Status | Evidence |
| --- | --- | --- |
| 2A workspace | implemented | topics, per-owner material attachment, grounded sessions with citations, saved sessions, plan progress; `ar`/`fr` with `dir` flip |
| 2B questions | implemented | deterministic cloze generation from real segments; feedback separates supported from unestablished; `graded_semantically: false` always |
| 2C flashcards | implemented | SM-2 in `services/study/scheduling.py`, documented, ease floored at 1.3; append-only history; boundary tests |
| 2D study plans | implemented | user-defined dates, four states, progress as a plain countable ratio, deleted material reported |
| 2E security | implemented | cross-user 404s on every resource, wrong-topic pairs, other-user documents, injection stays data, oversized inputs, persistence read straight from the DB |
| 2F frontend | implemented | 16 tests: citations, both insufficient-evidence paths, feedback wording, deleted source labelled, reveal-then-rate, plan PATCH, RTL |

`tests/test_study.py` — **39 tests**. Frontend — **16 tests**.

**No learning-outcome optimality is claimed for SM-2.** The scheduling module says
so explicitly.

### A defect this work introduced, caught by a test

`ck_materials_exactly_one_target` required exactly one of `document_id` /
`source_id` to be non-NULL, but the FK uses `ON DELETE SET NULL`. Deleting a
document attached to a topic fired an UPDATE nulling `document_id`, leaving both
NULL and violating the check — so **document deletion returned 500**. The
constraint and the foreign key action contradicted each other. Changed to "at most
one"; both-NULL is now the tombstone state meaning the target was deleted.
Exactly-one is enforced in the service at creation time.

## Stage 3 — cross-phase integration: **PARTIAL**

Done: provenance preserved on every grounded answer, question and card; deleting
a source marks dependent flashcards and material unavailable rather than leaving
a locator that looks checkable; isolation enforced in backend queries; question
generation fails with 409 on an empty topic instead of fabricating material;
existing ingestion/lifecycle/concurrency behaviour intact (614 tests green).

**Not done: research results cannot yet be saved into a study workspace.** There
is no path from `/research` into a topic. This is a stated gap, not an implicit
one.

## Stage 4 — verification

| Check | Result |
| --- | --- |
| Backend pytest | **614 tests, 0 failures, 0 errors, 1 skipped**, 101.060s, exit 0 |
| Concurrency on PostgreSQL 16.2 | 5/5, exit 0 |
| Migrations up/down/up (0003 and 0004) | exit 0 / 0 / 0, tables and index verified |
| `ruff check .` (from `apps/api`, as CI runs it) | All checks passed |
| `ruff format --check .` | 88 files already formatted |
| `mypy src` (strict, `MYPYPATH=src`) | Success, 55 source files |
| pip-audit | No known vulnerabilities found |
| Frontend typecheck | exit 0 |
| Frontend lint | 0 problems (`--max-warnings 0`) |
| Frontend tests | **94 passed, 7 files** |
| Frontend build | ✓ Compiled successfully; routes include `○ /study` |
| `npm audit --omit=dev` | 0 vulnerabilities |
| `npm audit` (full) | 5 high — `braces`, dev/lint-only, no patched release; accepted and documented |
| CI run 39871958816 on `0cded42` | **success, 4/4 jobs** |

The 1 skipped test is the opt-in live-source test.

### A CI failure I caused and fixed

Runs `39869839647`, `39871629985` and the one before them **failed** on the Ruff
lint step while every local check I ran was green. Cause: CI runs `ruff check .`
from `apps/api`, which includes `alembic/`; I had been running
`ruff check src tests tools` and never covered the migration directory. Fixed in
`0cded42`.

## Remaining risks and open items

1. **Stage 1D SSRF review not performed** — no new evidence this session.
2. **Research results are not savable into a study workspace** — Stage 3 gap.
3. **Live source retrieval unverified** — sandbox egress is allowlisted; nothing
   was observed on real content.
4. **`claim_support_honest` is 83.3%** — the engine asserts grounding on passages
   a human judged non-supporting. The UI must not present those as verified
   support.
5. Malware scanning absent (disclosed, fails closed, detection unverified — no
   engine installable here). Docker/Compose unverified (no runtime available).
   Backups unverified.
6. Quotas, rate limits and the worker remain **process-local** — single API
   process only.
7. `braces` advisory has no patched release; dev-only, accepted.

A passing test suite does not prove the absence of vulnerabilities.

## Release recommendation

**Do not release as a complete product.** Phases 6 (editable PPTX) and 7
(administration/deployment readiness) are not started, Stage 1D is open, and the
Stage 3 research-to-workspace path is missing.

The Phase 1–5 subset is **STAGING ONLY**, and never with more than one API
process, because quota, rate-limit and worker state is in-process memory.
