# MAFHUM

**منصة تعليمية وبحثية للطلبة — الجزائر أولًا، ثم العالم العربي.**

MAFHUM مشروع ويب عربيّ أولًا يساعد الطلبة على فهم الدروس والعمل على بحوث أكاديمية موثقة وإعداد Fiche TD والعروض التقديمية القابلة للتعديل. الواجهة عربية باتجاه RTL، مع دعم الفرنسية للمصادر والمخرجات.

> **الحالة الحالية:** تأسيس الوثائق والمعمارية. لا تُعد الميزات مكتملة إلا بعد تنفيذها واجتياز بوابات الاختبار في المستودع.

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

## Local development & verified results (Phases 1–2)

All commands below were executed against this codebase with real exit codes recorded.

### Prerequisites
- Node.js ≥ 20 and npm (verified on Node `22.22.3`, npm `10.9.8`)
- Python 3.11+ and venv (verified on Python `3.11.2`)
- Docker Compose (`docker-compose.yml`) is statically validated with `PyYAML`, while live multi-process verification was run directly via `uvicorn` + `next start` + Alembic SQLite because no Docker daemon is installed in this sandbox environment.

### Frontend (`apps/web`)
```bash
cd apps/web
npm ci                   # exit 0
npm run lint             # ESLint — exit 0
npm run typecheck        # tsc --noEmit — exit 0
npm test                 # Vitest — 6 files, 14 passed, exit 0
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
mypy app migrations tests     # Success: no issues found in 50 source files — exit 0
python -m pytest -v           # 28 passed — exit 0 (auth, CSRF, sessions, IDOR, quotas, deletion, migrations)
pip-audit                     # No known vulnerabilities found — exit 0
```

### Migrations (non-destructive upgrade & downgrade verified)
```bash
DATABASE_URL=sqlite:////tmp/mafhum.db PYTHONPATH=apps/api \
  python -m alembic -c apps/api/alembic.ini upgrade head
# Applies 0001_initial -> 0002_identity_and_projects without destroying pre-existing data
```

### Local stack (Docker Compose)
```bash
cp .env.example .env
docker compose up
```
Starts `db` (PostgreSQL 15), `api` (FastAPI on `127.0.0.1:8000`) and `web` (Next.js on `127.0.0.1:3000`, proxying `/api/v1/*` and `/health/*` to `http://api:8000`). No paid API keys or model downloads required.

### Security & dependency audit summary / remaining risks (Phase 2)
- **Python (`pip-audit`)**: **0 known vulnerabilities** (`fastapi==0.159.0`, `starlette==1.9.0`, `pytest==9.1.1`, `bcrypt==4.3.0`, `pip>=26.2.2`, `setuptools>=84.2.1`).
- **Node production runtime (`npm audit --omit=dev`)**: **0 vulnerabilities**.
- **Node dev-only lint toolchain (`npm audit` including devDependencies)**: reports 5 advisory nodes (`braces` → `micromatch` → `fast-glob` → `@next/eslint-plugin-next` → `eslint-config-next`) all rooted in a single unpatched upstream advisory (`GHSA-vfj7-8cjw-p6xm`, `braces <= 3.0.3`, CWE-674 stack exhaustion on untrusted brace patterns; `3.0.3` is the newest published version on npm). `npm audit fix --force` proposes an incompatible major downgrade (`eslint-config-next@14.2.35` on Next.js 16.4.2) and was intentionally declined. Exposure is restricted strictly to build-time linting over repository source files, never shipped or executed at runtime.
- **Rate limiter topology**: `SlidingWindowLimiter` is in-process memory for the single-instance modular monolith; multi-replica production deployments should back rate limiting with shared storage (e.g. Redis) behind the same interface.
- **Docker daemon**: unavailable in the current sandbox (`which docker` exit 1); live full-stack end-to-end HTTP smoke testing (`http://127.0.0.1:3000` → Next.js rewrite proxy → `http://127.0.0.1:8000` → migrated SQLite DB) was executed directly and passed (`exit 0`), while `docker compose up` must still be run on a host with Docker installed.
- **AI provider**: remains the deterministic offline `mock` adapter until evaluated model adapters are added in later phases.

## Status
- [x] مستودع ومعمارية أولية
- [x] قواعد إعادة استخدام OSS وفحص التراخيص
- [x] CI وskeleton قابل للتشغيل (واجهة + API + مزود تجريبي deterministic؛ نتائج lint والنوع والاختبارات والبناء موثقة في هذا الملف)
- [x] المرحلة 2: الهوية الآمنة والجلسات والملف الشخصي والمشاريع CRUD والحصص وتصدير/حذف الحساب مع اختبارات العزل والأمان
- [ ] الحسابات ومشاريع الطلبة
- [ ] إدخال المستندات والمصادر
- [ ] البحث المستند إلى الأدلة
- [ ] المساعد التعليمي وTD
- [ ] توليد PPTX واختبار RTL
- [ ] المراجعة الأمنية وتجربة النشر
