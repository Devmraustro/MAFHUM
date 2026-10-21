# Open-source Reuse Register

Preliminary review only: this records evidence inspected and candidate decisions. It does not mean code has been integrated or locally tested.

| Candidate | Evidence inspected | Proposed use | Decision / gate |
|---|---|---|---|
| [ScholaRAGent](https://github.com/ShiYu0318/ScholaRAGent) | README and LICENSE; MIT. README describes academic multi-stage RAG, injectable components and deterministic tests. Quick start treats Groq as required. | Architecture reference and possible selective research/retrieval modules. | Conditional. Inspect full source, dependencies, activity, security, data model and tests. Remove mandatory Groq assumption; retain MIT notice. Upstream test badges are claims from README, not local results. |
| [Docling](https://github.com/docling-project/docling) | README, LICENSE and pyproject.toml; MIT. Document parsing/layout/table/OCR support. | Parser adapter for PDF/DOCX and scanned-doc fallback. | Candidate. Benchmark Arabic/French digital/scanned PDF, tables, reading order, latency and memory. |
| [PptxGenJS](https://github.com/gitbrent/PptxGenJS) | LICENSE and package.json; MIT; metadata version 4.0.1 at inspection. | Editable PPTX generation. | Candidate; test actual Arabic RTL, mixed scripts, citations, charts and PowerPoint/LibreOffice output. |
| [OpenTutor](https://github.com/zijinz456/OpenTutor) | README, LICENSE and SECURITY.md; MIT. README calls it local single-user beta; security policy requires auth and strong JWT secret before public exposure. | Learning UX/feature reference. | Reference only unless multi-user isolation and security are separately proven; do not deploy/copy wholesale. |
| [Marp CLI](https://github.com/marp-team/marp-cli) | package.json; MIT, version 4.5.1 at inspection. | Comparison candidate for slide workflow. | Benchmark only; PPTX editability and RTL must pass. |
| [Qwen3-Embedding](https://github.com/QwenLM/Qwen3-Embedding) | README inspected; describes multilingual embedding/reranking for 100+ languages. Exact weights license not confirmed in this review. | Optional embedding/reranking. | Not approved before exact model card/weights license, memory and Arabic/French benchmark. |
| [Haystack](https://github.com/deepset-ai/haystack) | Not fully audited in this pass. | Optional RAG orchestration. | Do not add until evaluated complexity justifies it; inspect exact version/license/security first. |

## Adoption template
Record upstream URL, pinned commit/tag, date, exact license URL/text, files/modules used, notices, modifications, security/advisories, tests run, maintenance signal, rationale, risks and decision.

## Phase 2 adoption decisions (secure identity, sessions, projects)

| Candidate | Evidence inspected | Proposed use | Decision / gate |
|---|---|---|---|
| [pyca/bcrypt](https://github.com/pyca/bcrypt) | GitHub: Apache-2.0 license, PyCA (Python Cryptographic Authority), Development Status 5 - Production/Stable, Python 3.9+, recent maintenance (updated Jan/Aug 2026), recent release 5.0.0 (2026). PyPI shows pre-built wheels for 4.3.0; 5.0.0 requires a Rust toolchain to build from source. | Password hashing for user accounts. | **Adopted.** Pin `bcrypt==4.3.0` (Apache-2.0) — latest 4.x with pre-built wheels, avoids Rust build requirement. Never store plaintext passwords. |
| [jpadilla/pyjwt](https://github.com/jpadilla/pyjwt) | GitHub: MIT license, actively maintained (2.15.1, Sep 2026), recent security release 2.13.0 (2026-05-21). | JWT-based sessions. | **Declined for Phase 2.** Database-backed sessions with random tokens (secrets.token_urlsafe) support explicit logout/revocation and expiration without JWT complexity. Reconsider if stateless sessions are later required. |
| [laurentS/slowapi](https://github.com/laurentS/slowapi) | GitHub: MIT license; latest release 0.1.10 (Jun 2026); known open issue #281: rate limiting broken on FastAPI >= 0.137, no fix released as of Sep 2026. | Rate limiting for auth endpoints. | **Declined for Phase 2.** MAFHUM pins FastAPI 0.116.1 (unaffected today), but pinning FastAPI solely to keep slowapi working is a maintenance risk; unresolved upstream issue. Implemented a small, fully tested in-memory sliding-window limiter for /auth/register and /auth/login instead. Re-evaluate slowapi when the FastAPI incompatibility is resolved upstream. |
| [fastapi-users/fastapi-users](https://github.com/fastapi-users/fastapi-users) | GitHub: MIT license; project in maintenance mode (security/dependency updates only, no new features); last release changed password hashing from passlib to pwdlib (breaking change). | Full auth framework (register/login/reset/OAuth). | **Declined.** Maintenance-mode, breaking dependency changes, and over-scoped for Phase 2 (email/OAuth flows we do not yet need). Implemented register/login/sessions directly with bcrypt + DB sessions. |

## Restricted/rejected (unchanged)
- Do not use PaperLens community-license code without written permission and legal review.
- Do not add AGPL/GPL applications without compatibility/deployment-obligations review.
- Commerce, video, trading and device-sync projects are outside scope unless a demonstrated product need exists.

