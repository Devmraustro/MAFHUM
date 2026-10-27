# Security policy

MAFHUM is an early-stage, Arabic-first research and study platform. This file
describes how to report a problem and what is actually in scope. It is not a
bug-bounty programme and does not promise a response SLA.

## Reporting a vulnerability

**Please do not open a public issue for a security problem.** Use GitHub's
private vulnerability reporting ("Report a vulnerability" under the Security
tab) so the details are not disclosed before a fix exists.

Include:

- the affected component and version/commit,
- a minimal reproduction (no real user data),
- the impact you believe it has,
- whether you have already disclosed it anywhere else.

Never include real credentials, session cookies, uploaded documents or personal
data in a report. Synthetic examples only.

## What is in scope

- Authentication, sessions, CSRF and the origin allowlist.
- Tenant isolation: reading, downloading or deleting another user's documents,
  sources, study material or presentations.
- Upload validation, storage-key traversal, archive/parser bounds and the
  `clamd` scanning policy.
- Grounded-research citation integrity (invented or mis-attributed references).
- Generated PPTX artefacts (OOXML injection, path traversal, size/DoS bounds).
- The admin role gate, audit-event integrity and redaction of audit details.
- SSRF in the opt-in external source retrieval path.
- Dependency vulnerabilities in pinned runtime packages.

## Known limitations, not vulnerabilities to re-report

These are documented openly in `docs/THREAT_MODEL.md`,
`docs/SECURITY_ASSESSMENTS.md` and `docs/STATUS_REPORT_2026-10-27.md`:

- No OCR; scanned PDFs are detected and reported, not read.
- No malware engine ships by default; `clamd` must be provisioned and verified.
- No encryption at rest; filesystem permissions only.
- No generative model, no account export, no real publisher retrieval.
- No alerting/monitoring system is deployed; admin metrics are a scrape source.
- Five high dev-tree advisories were previously reported for `braces`; a
  2026-10-27 re-audit reports 0, and the triage is retained in the threat model.
- `psycopg` is LGPL-3.0-only (permissive enough for an unmodified import) — see
  `THIRD_PARTY_NOTICES.md`.

## Verification expectations

Any security claim in this repository must come from an executed command or a
recorded CI run with its ID. A passing test suite is not proof of the absence of
vulnerabilities, and no penetration test or fuzzing campaign has been performed.

## Supported versions

Only `main` and the current working branch receive fixes. There is no released
version line yet and no published package.
