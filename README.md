# MAFHUM

**منصة تعليمية وبحثية للطلبة — الجزائر أولًا، ثم العالم العربي.**

MAFHUM مشروع ويب عربيّ أولًا يساعد الطلبة على فهم الدروس والعمل على بحوث أكاديمية موثقة وإعداد Fiche TD والعروض التقديمية القابلة للتعديل. الواجهة عربية باتجاه RTL، مع دعم الفرنسية للمصادر والمخرجات.

> **الحالة الحالية:** اكتمال المرحلة 2 ومراجعة التحصين الأمني للمرحلة 2 (Phase 2 Security Hardening Review) قبل البدء بالمرحلة 3. لا تُعد الميزات مكتملة إلا بعد تنفيذها واجتياز بوابات الاختبار في المستودع.

## المبادئ
- الاستناد إلى مصادر جزائرية رسمية وأكاديمية مسموح باستخدامها، مع روابط ومراجع يمكن التحقق منها.
- عدم اختلاق المراجع أو أرقام الصفحات أو النصوص القانونية.
- اعتبار أي مخرجات AI مسودات تحتاج إلى مراجعة الطالب.
- حماية الملفات الشخصية والبحوث وعزل بيانات كل مستخدم.
- إعادة استخدام مكونات مفتوحة المصدر بعد مراجعة الترخيص والأمان والاختبار.
- تطوير نسخة أولى مجانية دون اشتراط API مدفوع أو الوعد باستضافة inference مجانية بلا حدود.

## الميزات المستهدفة
- إنشاء خطة بحث وصياغة أقسام البحث انطلاقًا من أدلة موثوقة.
- إنشاء Fiche TD وإشكالية وخطة.
- شرح مبسط وأمثلة بديلة وأسئلة لفحص الفهم.
- إنشاء عروض PPTX قابلة للتعديل مع ضبط RTL والمراجع.
- حفظ المشاريع ورفع الملفات وتصدير النتائج بعد تفعيل الحسابات والأمن.
- لوحة إدارة وحدود استخدام وسياسة حذف وخصوصية.

## المصادر المستهدفة
مرشحات أولية: الجريدة الرسمية الجزائرية (JORADP)، وزارة التعليم العالي (MESRS)، ASJP، SNDL ومستودعات الجامعات. يجب التحقق من شروط كل مصدر؛ وجود رابط عام لا يعني السماح بجمع النص الكامل أو إعادة نشره.

## التقنية المقترحة
- Frontend: Next.js + TypeScript، واجهة RTL.
- API: Python + FastAPI + Pydantic.
- Database: PostgreSQL مع migrations.
- Document parsing: Docling بعد benchmark عربي/فرنسي.
- Editable PPTX: PptxGenJS بعد اختبار RTL.
- Retrieval: lexical baseline؛ embeddings/vector index فقط إن أثبتت الاختبارات فائدتها.
- Provider-neutral AI gateway مع mock للاختبار وموصل محلي اختياري.

تفاصيل المعمارية وخطة التنفيذ في:
- [Project Architecture](docs/PROJECT_ARCHITECTURE.md)
- [Roadmap](docs/ROADMAP.md)
- [Threat Model](docs/THREAT_MODEL.md)
- [Data Policy](docs/DATA_POLICY.md)
- [OSS Reuse Register](docs/OSS_REUSE_REGISTER.md)
- [Arena Master Prompt](ARENA_MASTER_PROMPT.md)

## Local development & verified results (Phases 1–2 + Phase 2 Security Hardening)

All commands below were executed against this codebase with real exit codes recorded.

### Prerequisites
- Node.js ≥ 20 and npm (verified on Node `22.22.3`, npm `10.9.8`)
- Python 3.11+ and venv (verified on Python `3.11.2`)
- PostgreSQL 15/16: verified locally against embedded `PostgreSQL 16.2` (`pgserver==0.1.4` dev/test fixture) AND in GitHub Actions CI against `postgres:15-alpine`.
- Docker Compose (`docker-compose.yml`) is statically validated with `PyYAML`, while live multi-process full-stack verification was executed directly via `PostgreSQL 16.2` + `uvicorn` + `next start` because no Docker daemon is installed in this sandbox environment.

### Frontend (`apps/web`)
```bash
cd apps/web
npm ci                   # exit 0
npm run lint             # ESLint — exit 0
npm run typecheck        # tsc --noEmit — exit 0
npm test                 # Vitest — 6 files, 15 passed (incl. __Host-mafhum_csrf & SafeErrorEnvelope), exit 0
npm run build            # Next.js 16 build — 10/10 static pages prerendered, exit 0
npm audit --omit=dev     # 0 runtime vulnerabilities, exit 0
```

### Backend (`apps/api`)
```bash
cd apps/api
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade "pip>=26.2.2" "setuptools>=84.2.1"
pip install -r requirements.txt -r requirements-test.txt
ruff check .                  # All checks passed — exit 0
mypy app migrations tests     # Success: no issues found in 55 source files — exit 0
python -m pytest -v           # 40 passed — exit 0 (SQLite + real PostgreSQL 16 migrations, FKs, cascades, select-for-update concurrency, IP/account rate limits, CSRF/Origin/cookie-injection, secret rotation, redaction)
pip-audit                     # No known vulnerabilities found — exit 0
```

### Migrations (non-destructive upgrade & downgrade verified on both SQLite and PostgreSQL)
```bash
cd apps/api
# SQLite local dev/test database:
DATABASE_URL=sqlite:////tmp/mafhum.db PYTHONPATH=. \
  .venv/bin/alembic -c alembic.ini upgrade head

# PostgreSQL database:
DATABASE_URL=postgresql+psycopg://mafhum:password@127.0.0.1:5432/mafhum PYTHONPATH=. \
  .venv/bin/alembic -c alembic.ini upgrade head
# Applies 0001_initial -> 0002_identity_and_projects -> 0003_quota_check_constraint
# (reconciles users.projects_count and adds ck_users_projects_within_quota cleanly on both engines)
```

### Local stack (Docker Compose)
```bash
cp .env.example .env
docker compose up
```
Starts `db` (PostgreSQL 15), `api` (FastAPI on `127.0.0.1:8000`) and `web` (Next.js on `127.0.0.1:3000`, proxying `/api/v1/*` and `/health/*` to `http://api:8000`). No paid API keys or model downloads required.

### Security & dependency audit summary / remaining risks (Phase 2 Hardened)
- **Account & IP rate limiting**: Login enforces two independent limiters — a per-account sliding-window limit keyed strictly by `HMAC-SHA256(SESSION_SECRET, NFKC(lower(strip(email))))` (zero raw email PII stored in limiter buckets; unaffected by IP rotation) and an independent per-IP limit (`AUTH_IP_RATE_LIMIT`, stopping password spraying across distinct accounts). Proxy forwarding headers (`X-Forwarded-For`, `X-Real-IP`, `X-Forwarded-Proto`) are ignored unless the TCP peer matches configured `TRUSTED_PROXY_IPS` CIDRs (right-to-left scan).
- **Concurrency & database portability**: Project quota mutations lock the parent `users` row (`SELECT ... FROM users WHERE id = :uid FOR UPDATE` on PostgreSQL `READ COMMITTED`) before counting/inserting and enforce `ck_users_projects_within_quota` (`projects_count <= project_quota`) inside the same transaction. Concurrent duplicate-email registrations catch `IntegrityError`, roll back cleanly, and return `409 Conflict` (`SafeErrorEnvelope`). Note: SQLite local dev uses database-level single-writer locking (`busy_timeout=30000`) and is intentionally **not** treated as equivalent to multi-process PostgreSQL MVCC concurrency; production requires PostgreSQL (enforced at startup).
- **CSRF, Origin, `__Host-` cookies, and secret rotation**: Every cookie-authenticated state-changing route enforces `Origin`/`Referer` allowlist verification AND session-bound `HMAC-SHA256` double-submit CSRF tokens (`new_csrf_token(session_token)`), neutralizing subdomain cookie-tossing/injection attacks. Production automatically sets `Secure`, `HttpOnly`, `SameSite=lax`, and `__Host-` cookie prefixes (`__Host-mafhum_session`, `__Host-mafhum_csrf`). Rotating `SESSION_SECRET` immediately invalidates all active server session digests and CSRF tokens.
- **Python (`pip-audit`)**: **0 known vulnerabilities** (`fastapi==0.159.0`, `starlette==1.9.0`, `pytest==9.1.1`, `bcrypt==4.3.0`, `pgserver==0.1.4`, `pip>=26.2.2`, `setuptools>=84.2.1`).
- **Node production runtime (`npm audit --omit=dev`)**: **0 vulnerabilities**.
- **Node dev-only lint toolchain (`npm audit` including devDependencies)**: reports 5 advisory nodes (`braces` → `micromatch` → `fast-glob` → `@next/eslint-plugin-next` → `eslint-config-next`) rooted in `GHSA-vfj7-8cjw-p6xm` (`braces <= 3.0.3`, CWE-674; newest published version on npm). `npm audit fix --force` proposes an incompatible major downgrade (`eslint-config-next@14.2.35` on Next.js `16.4.2`) and was declined. Build-time lint only; zero runtime exposure.
- **Rate limiter topology**: `SlidingWindowLimiter` runs in-process for the single-instance modular monolith; multi-worker / multi-replica deployments must back rate limiting with shared storage (e.g. Redis) behind the same interface so per-account/per-IP budgets are global across workers.
- **Docker daemon**: unavailable inside the current sandbox (`which docker` exit 1); live full-stack end-to-end HTTP smoke testing (`http://127.0.0.1:3000` → Next.js rewrite proxy → `http://127.0.0.1:8000` → real `PostgreSQL 16.2`) was executed directly and passed (`exit 0`), while `docker compose up` must still be run on a host with Docker installed.

## Status
- [x] مستودع ومعمارية أولية
- [x] قواعد إعادة استخدام OSS وفحص التراخيص
- [x] CI وskeleton قابل للتشغيل (واجهة + API + مزود تجريبي deterministic؛ نتائج lint والنوع والاختبارات والبناء موثقة في هذا الملف)
- [x] المرحلة 2 ومراجعة التحصين الأمني للمرحلة 2: الهوية الآمنة والجلسات وحماية CSRF/Origin/Cookie-Injection وتحديد المعدل المستقل عن IP وتزامن الحصص على PostgreSQL وتصدير/حذف الحساب
- [ ] إدخال المستندات والمصادر (المرحلة 3)
- [ ] البحث المستند إلى الأدلة (المرحلة 4)
- [ ] المساعد التعليمي وTD (المرحلة 5)
- [ ] توليد PPTX واختبار RTL (المرحلة 6)
- [ ] المراجعة الأمنية النهائية وتجربة النشر (المرحلة 7)
