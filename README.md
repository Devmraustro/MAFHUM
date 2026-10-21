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

## Local development (Phase 1 — results recorded)

All commands below were run and passed in this repository; exit codes are real.

### Prerequisites
- Node.js ≥ 20 and npm (tested with Node 22.22.3, npm 10.9.8)
- Python 3.11+ and venv (tested with Python 3.11.2)
- Docker Compose is documented but **not run locally** (no docker daemon in this environment); see the deployment note below.

### Frontend (`apps/web`)
```bash
cd apps/web
npm ci
npm run lint        # ESLint — exit 0
npm run typecheck   # tsc --noEmit — exit 0
npm test            # vitest — 9 passed, 3 files, exit 0
npm run build       # next build — 5 routes prerendered, exit 0
```

### Backend (`apps/api`)
```bash
cd apps/api
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-test.txt
ruff check .        # exit 0
mypy app migrations # exit 0
python -m pytest    # 14 passed, exit 0
```

### Migrations (offline, no database credentials)
```bash
DATABASE_URL=sqlite:////tmp/mafhum.db PYTHONPATH=apps/api \
  python -m alembic -c apps/api/alembic.ini upgrade head
# INFO ... Running upgrade  -> 0001_initial
```

### Local stack (Docker Compose)
```bash
docker compose up
```
Starts `db` (PostgreSQL 15), `api` (FastAPI on 127.0.0.1:8000) and `web` (Next.js on 127.0.0.1:3000) from `.env` (copy `.env.example` to `.env`). Requires a docker daemon — unavailable in this environment, so the compose flow is statically reviewed and **not yet executed**. No model is downloaded and no paid credential is required.

### Known risks (Phase 1)
- `npm audit` reports 5 high-severity advisory findings in the dev-only toolchain (`braces` via eslint toolchain). Fixing them with `npm audit fix --force` downgrades `eslint-config-next` below what Next.js 16 requires — a breaking change — so they are left pinned and tracked, not force-fixed.
- Docker Compose untested locally (no docker daemon).
- The AI provider is the deterministic mock only; no vendor integration exists yet.

## Status
- [x] مستودع ومعمارية أولية
- [x] قواعد إعادة استخدام OSS وفحص التراخيص
- [x] CI وskeleton قابل للتشغيل (واجهة + API + مزود تجريبي deterministic؛ نتائج lint والنوع والاختبارات والبناء موثقة في هذا الملف)
- [ ] الحسابات ومشاريع الطلبة
- [ ] إدخال المستندات والمصادر
- [ ] البحث المستند إلى الأدلة
- [ ] المساعد التعليمي وTD
- [ ] توليد PPTX واختبار RTL
- [ ] المراجعة الأمنية وتجربة النشر
