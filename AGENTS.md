# MAFHUM Agent Rules

- Read `docs/PROJECT_ARCHITECTURE.md` and `ARENA_MASTER_PROMPT.md` before changes.
- Product name is MAFHUM only.
- Search GitHub and review README, exact LICENSE, manifests, security posture, tests and maintenance before reimplementing a substantial capability.
- Record reuse decisions in `docs/OSS_REUSE_REGISTER.md`; preserve upstream notices.
- Never introduce mandatory paid APIs or commit secrets.
- Never invent citations or claim checks passed unless actually run.
- Enforce server-side authorization and tenant isolation for all user resources.
- Prioritize Arabic RTL, French mixed-script behavior, accessibility and privacy.
- Implement one vertical slice at a time; do not weaken tests to get green CI.
- Do not force-push, rewrite history, or delete user data.
