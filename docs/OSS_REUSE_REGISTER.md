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

## Restricted/rejected
- Do not use PaperLens community-license code without written permission and legal review.
- Do not add AGPL/GPL applications without compatibility/deployment-obligations review.
- Commerce, video, trading and device-sync projects are outside scope unless a demonstrated product need exists.
