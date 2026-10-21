# MAFHUM Data Policy (Implementation Requirements)

## User data
Collect only data needed for accounts, saved work, usage and support. Every project/document has explicit ownership. Do not train on student documents by default. Do not send uploads to external inference providers unless configured and disclosed.

## Uploads and retention
Validate uploads and store privately. Record provenance and processing status. Define retention by data class and implement deletion of originals, extracted text, chunks, embeddings, caches and generated artifacts. Support export and account deletion. Do not put document contents or credentials in ordinary logs.

## Source use
Each connector documents terms, access boundary, allowed metadata/full text/excerpts, attribution, update cadence and takedown handling. No paywall/authentication bypass. Link to restricted content rather than indexing it unless permitted. Keep canonical URL, checksum and source/version dates.

## AI processing
All output is a draft. Use a provider-neutral gateway and disclose configured provider/data path. Do not claim data stays local unless actual runtime configuration proves it. Enforce quotas and output limits.

## Academic integrity
Every citation must resolve to a source record and supporting excerpt or page/section when available. Never invent authors, pages, URLs, titles, quotations or legal provisions. Mark unverified details for student review and encourage reading original sources.

## Logging/access
Least privilege, redacted logs, audit privileged access, and avoid storing prompts/responses unless required for saved work and covered by retention rules.

Before public release, review this policy against applicable Algerian law, provider terms and actual deployment. This document is not legal advice or a compliance certification.
