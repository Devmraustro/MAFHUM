# ADR 0001: Start with a modular monolith

- Status: Proposed baseline decision
- Date: 2026-10-21

## Context
MAFHUM is a new product with a 0 DZD MVP budget and a small initial scope, but it needs distinct domains for identity, documents, academic sources, retrieval, tutoring, presentations and administration. Microservices would add deployment, observability and operations overhead before scale or boundaries have been demonstrated.

## Decision
Use a modular monolith with a Next.js/TypeScript frontend and a FastAPI/Python API. Organize backend domains behind service and repository interfaces. Use PostgreSQL migrations for shared multi-user data and private object storage through an adapter. Expensive parsing and generation use bounded jobs. Define an AI provider interface and deterministic mock.

## Consequences
- Lower local-development and hosting complexity.
- Clear domain seams permit a future service split if measured scaling or reliability needs justify it.
- Modules must not bypass service boundaries or share arbitrary mutable state.
- Hosting constraints and model inference remain explicit; heavy inference may be local or separately hosted and is not promised free or always-on.
- Revisit only with measurements showing a modular monolith is insufficient.
