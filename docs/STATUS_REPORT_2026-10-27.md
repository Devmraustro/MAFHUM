# تقرير فحص شامل لحالة مشروع MAFHUM — ما تبقّى حتى الإنجاز

**التاريخ:** 2026-10-27 (UTC)
**الفرع قيد الفحص:** `arena/71aa7382-mafhum` (= `main` حالياً)
**المستودع:** https://github.com/Devmraustro/MAFHUM
**نوع التقرير:** تدقيق حالة (status audit) — بدون أي تعديل على الكود أو الدمج.

> **تحديث 2026-10-28:** هذا التقرير تاريخي ويصف حالة 2026-10-27. راجع **§15** في
> آخر الملف للوقائع المحدَّثة: تم تنفيذ `docker compose up` فعلياً في CI، وأُضيفت
> خدمة ClamAV إنتاجية وتصدير الحساب والاحتفاظ بالسجلات وتصميم التشفير ومراقبة
> مقيّدة النطاق وبوابات أمنية. بعض أسطر الجداول أدناه (مثل «Compose غير مُختبَر»)
> لم تعد صحيحة؛ §15 يصحّحها صراحةً.

> ملاحظة منهجية مهمة: كل ما يلي مبني على **أدلة مرصودة** من شجرة الملفات و`git` وواجهة GitHub (`gh`).
> لم أُعِد تشغيل اختبارات المشروع محلياً في هذه الجلسة؛ نتائج الاختبارات المذكورة أدناه **مأخوذة من سجلات GitHub Actions التي تحققت من حالتها** عبر `gh run view` / `gh pr checks`، ومن تقارير الفرع نفسه. لا أدّعي اجتياز اختبار لم أرَهُ مسجّلاً.

---

## 1. الخلاصة التنفيذية (اقرأ هذا أولاً)

**الاكتشاف الأهم:** المشروع ليس "ناقص الميزات" بالدرجة الأولى — بل **منفّذ إلى حد كبير لكنه محبوس خارج الفرع الرئيسي**.

| الحقيقة | الدليل |
|---|---|
| `main` (وفرع جلستنا الحالي) يحتوي **13 ملفاً فقط، كلها وثائق**، وcommit واحد (`c4c1e3b`، 2026-10-21) = **Phase 0 فقط** | `git ls-files` (13 ملفاً)، `find` (لا يوجد أي `.py`/`.ts`/`package.json`/`Dockerfile`) |
| العمل الحقيقي (Phase 1 → Phase 5 + تقوية أمنية) موجود على فرع `arena/1cb7b9d2-mafhum` في **PR #1 مفتوح منذ 2026-10-22** | `gh pr view 1`: **171 ملفاً متغيّراً، +43,322 سطراً**، الحالة `OPEN`، `MERGEABLE`، `mergeStateStatus: CLEAN`، **بدون أي مراجعة (reviewDecision فارغ)** |
| هذا العمل **اجتاز كل البوابات** | CI: تشغيل `40397915589` — **4/4 jobs خضراء** (baseline / backend / migrations / frontend) على الـ HEAD النهائي `2719689`. بوابات Staging: تشغيل `40377229156` — **A وB وC خضراء** |
| إذن: **5 مراحل من أصل 8 منفّذة ومختبرة، لكنها غير موجودة على `main`** | `docs/ROADMAP.md` على فرع PR #1 |

**النتيجة العملية:** أول عائق أمام "الإنجاز" ليس برمجياً بل **حوكمي**: دمج PR #1. أي محاولة للبناء فوق الفرع الحالي (`arena/71aa7382-mafhum`) ستعيد اختراع ما أُنجز بالفعل.

**ما تبقّى فعلياً بعد الدمج:** Phase 6 (عروض PPTX — **لم تبدأ إطلاقاً**)، Phase 7 (إدارة/نشر — **جزئية**)، مجموعة **تناقضات في الوثائق** يجب تصحيحها، و**فجوات/قيود تقنية** إما تُغلق أو يُعلَن قبولها رسمياً.

---

## 2. خريطة الفروع (صورة كاملة)

| الفرع | HEAD | التاريخ | المحتوى | الحالة |
|---|---|---|---|---|
| `main` | `c4c1e3b` | 2026-10-21 | 13 ملف وثائقي فقط (Phase 0) | الفرع الافتراضي |
| `arena/71aa7382-mafhum` | `c4c1e3b` | — | مطابق لـ `main` | **فرع جلستنا الحالية** — فارغ من الكود |
| `arena/dee89266-mafhum` | `da14f8e` | 2026-10-21 | 115 ملفاً: Phase 1 + Phase 2 | **قديم/محلّ محله**، بلا PR، لا يوجد سلف مشترك مباشر مع فرع Phase 3 |
| `arena/1cb7b9d2-mafhum` | `2719689` | 2026-10-26 | **174 ملفاً**: Phase 1→5 + تقوية + بوابات | **PR #1 مفتوح**، كل البوابات خضراء |

أيضاً: **لا توجد Issues، ولا Tags، ولا Releases، ولا LICENSE** (`gh repo view ... --json licenseInfo` → `null`)، ولا `SECURITY.md` / `CODEOWNERS`. لم أستطع التحقق من حماية الفرع `main` (الـ API أعاد 403) — لا أدّعي شيئاً بخصوصها.

---

## 3. ما أُنجز فعلاً (على فرع PR #1، بأدلة)

| المجال | الحالة | الدليل |
|---|---|---|
| Phase 0 — الوثائق والحوكمة | ✅ | CI job "Repository baseline" أخضر |
| Phase 1 — هيكل قابل للتشغيل | ✅ مع استثناءات | Next.js 16 + TS (`apps/web`)، FastAPI + Pydantic v2 (`apps/api`)، `/health/live` و`/health/ready`، طلبات بأرقام تتبع وأخطاء آمنة، 7 migrations |
| Phase 2 — الهوية والمساحة | ✅ تقريباً | تسجيل/دخول/خروج، جلسات، CSRF، مشاريع CRUD، عزل بين المستخدمين، حصص، حدّ محاولات دخول، **حذف حساب** |
| Phase 3 — المستندات والمصادر | ✅ مع قيود معلنة | رفع PDF/DOCX، تحقق بالـ magic bytes، تخزين خاص، استخراج وتطبيع وتقطيع عربي/فرنسي، عامل معالجة مقيّد، كتالوج مصادر بال provenance، حذف يتتبّع كل الأثر |
| Phase 4 — البحث الموثّق | ✅ مع قيود | استرجاع معجمي، **اقتباسات حرفية فقط** (لا نموذج لغوي)، حالة "أدلة غير كافية"، تقييم ذهبي v1 (24 حالة) وv2 (38 حالة) |
| Phase 5 — المساعد الدراسي/TD | ✅ مع قيود | مواضيع، أسئلة، بطاقات SM-2، خطط دراسية، واجهة `/study`، حفظ نتائج البحث داخل مساحة الدراسة |
| واجهة عربية RTL | ✅ | `apps/web/src/app` + صفحات `/documents`، `/research`، `/study`، لغة fr جاهزة |
| الاختبارات | ✅ | **850 اختبار backend** (0 فشل، 0 أخطاء، 4 متخطاة لأسباب شبكية معلنة) وفق `docs/STAGING_ACCEPTANCE_REPORT.md`؛ ruff / ruff-format / mypy-strict / pip-audit نظيفة؛ frontend typecheck/lint/tests/build نظيفة |
| بوابات النشر التجريبي | ✅ (في CI) | Gate A: ClamAV حقيقي + EICAR + رفض fail-closed؛ Gate B: بناء الحاوية + تشغيل غير جذر + readiness + إيقاف نظيف؛ Gate C: DNS/TCP/TLS موثّق للمصادر الخارجية |
| نسخ احتياطي/استعادة | ✅ | `apps/api/tools/backup.py` + اختبارات استعادة حقيقية، مع اختيار أدوات pg المطابقة لإصدار الخادم |

**قيود معلّنة بصراحة في وثائق الفرع (وهذه ميزة لا عيب):** لا OCR، لا محرك مكافحة فيروسات ضمن التثبيت الافتراضي، لا استيراد من URL، لا توليد إجابات بنموذج لغوي، لا نشر فعلي على بنية تحتية حقيقية.

---

## 4. العائق رقم 1: PR #1 غير مدموج

| البند | القيمة |
|---|---|
| الرابط | https://github.com/Devmraustro/MAFHUM/pull/1 |
| الحالة | `OPEN` منذ 2026-10-22، آخر تحديث 2026-10-26 |
| قابلية الدمج | `MERGEABLE` / `CLEAN` (لا تعارضات) |
| المراجعات | **صفر** (`reviews: []`)، **لا تعليقات** (`comments: []`) |
| checks | 4/4 خضراء على آخر commit |
| حجم التغيير | 171 ملفاً، +43,322 / −157 |

⚠️ **وصف الـ PR قديم ومضلّل الآن** (مكتوب عند Phase 3): يقول "المراحل 4–7 لم تبدأ"، "435 اختباراً"، "لا يوجد Docker/Compose"، "لا يوجد فحص فيروسات"، "لا يوجد mock AI gateway" — بينما الفرع نفّذ Phase 4 و5، و850 اختباراً، ويحتوي `Dockerfile` و`docker-compose.yml` وبوابة ClamAV حقيقية. **يجب تحديث الوصف قبل الدمج** حتى لا يُراجَع PR بوثيقة خاطئة.

⚠️ **قيد تقني بعد الدمج:** ملف `.github/workflows/staging-acceptance.yml` يعمل على `push` إلى الفرع المسمّى `arena/1cb7b9d2-mafhum` فقط (لأن `workflow_dispatch` لا يُسجّل إلا من الفرع الافتراضي). بعد الدمج **ستتوقف البوابات عن العمل تلقائياً** — يجب تعديل المشغّل إلى `main` + `workflow_dispatch`.

⚠️ آخر تشغيل أخضر لبوابات A/B/C كان على `3589dc8`، بينما HEAD النهائي `2719689` (به تعديل على `tools/backup.py` + وثائق). CI العادي أخضر على HEAD النهائي، لكن **البوابات الثقيلة لم تُعَد على آخر commit**.

---

## 5. تناقضات الوثائق (يجب حلّها قبل أي إصدار)

الوثائق على فرع PR #1 نمت عبر جلسات متعددة، فبقيت أقسام قديمة تناقض أقساماً محدّثة:

| الملف (على فرع PR #1) | النص القديم | الحقيقة المحدّثة |
|---|---|---|
| `README.md:134` و `.env.example` | "الحصص والعامل process-local — شغّل عملية API واحدة" | `docs/SECURITY_ASSESSMENTS.md:241` و ADR 0004 (تعديل 2026-10-23) و`docs/DEPLOYMENT.md`: **الحصص وحدّ الدخول أصبحت مُطبَّقة في قاعدة البيانات** وآمنة عبر عدة نسخ؛ القيد المتبقي هو **عامل المعالجة** فقط (قفل advisory: عامل واحد لكل قاعدة بيانات) |
| `docs/ROADMAP.md:35` | "Docker Compose غير متوفر" | `docker-compose.yml` موجود فعلاً (لكنه يحمل تعليق "NOT YET VERIFIED BY EXECUTION" — Gate B اختبر بناء الصورة وتشغيلها، **ولم يختبر `docker compose up` تحديداً**) |
| `docs/SECURITY_ASSESSMENTS.md:126,138,141` | "Docker غير موثّق / لا يوجد Dockerfile أو compose في المستودع" | Gate B **نجح** في بناء الحاوية وتشغيلها؛ `apps/api/Dockerfile` مُتتبَّع |
| `docs/THREAT_MODEL.md:33,165,171` | "لا يوجد فحص فيروسات"، "الحدود process-local" | `SECURITY_ASSESSMENTS.md:13` + Gate A: التكامل منفّذ والكشف **موثّق في بيئة القبول** (مع بقاء "التثبيت الافتراضي لا يحوي محركاً") |
| `docs/STAGING_ACCEPTANCE_REPORT.md` (الترويسة + §2) | "Verdict: CONDITIONAL STAGING"، وجدول فيه 1.1/2.1/2.3/2.4/2.6 = `BLOCKED` | نفس الملف في آخره: **A/B/C كلها PASS** — يجب رفع التناقض وتحديث الحكم النهائي |
| `docs/ROADMAP.md:45` | "حدّ الدخول process-local" | انظر أول سطر في هذا الجدول |
| وصف PR #1 | أرقام Phase 3 فقط | انظر القسم 4 |

**أيضاً:** `.env.example` لا يوثّق متغيرات `CLAMD_*` / `MALWARE_SCAN_REQUIRED` / `PROCESSING_WORKER_ENABLED` رغم أنها تُقرأ فعلياً؛ اختبار العقد الموجود يمنع المتغيرات الوهمية لكنه لا يضمن اكتمال التوثيق.

---

## 6. المتبقّي حتى الإنجاز — خطة أولويات

### P0 — الحوكمة (قبل كتابة أي ميزة جديدة) — الأهم الآن

1. **دمج PR #1 في `main`** (`gh pr merge 1 --merge`) بعد تحديث وصفه؛ ثم جلب الحالة إلى فرع الجلسة `arena/71aa7382-mafhum` (`git merge origin/main`) والعمل فوقها. *لم أنفّذ ذلك لأنك طلبت تقريراً فقط.*
2. **تصحيح محفّز بوابات الـ staging** وإعادة تشغيل A/B/C على HEAD النهائي بعد الدمج.
3. **توحيد الوثائق** وفق جدول القسم 5 + تحديث `docs/ROADMAP.md` و`README.md` لتعكس الوضع الحقيقي.
4. **إضافة ملفات الإصدار الناقصة:** `LICENSE` (قرار ترخيص المشروع نفسه — حالياً `licenseInfo: null`)، `SECURITY.md`، ومراجعة `THIRD_PARTY_NOTICES.md` مقابل التبعيات الفعلية (مثلاً `psycopg` LGPL-3.0-only، `pgserver` Apache-2.0 للتطوير فقط).
5. **معالجة الفرع القديم** `arena/dee89266-mafhum` (Phase 2، لم يعد مرجعاً) — أرشفة أو حذف **بعد موافقتك** فقط.
6. إنشاء Issues/Milestones لتتبّع Phase 6/7 بدلاً من الاعتماد على PR واحد ضخم.

### P1 — Phase 6: العروض التقديمية القابلة للتعديل (**لم تبدأ إطلاقاً**)

لا يوجد أي ملف `pptx`/`presentation` في `apps/`، ولا مسار `/presentations` في `router.py`.

- [ ] مراجعة OSS قبل التنفيذ: PptxGenJS (MIT، تثبيت إصدار محدّد)، مقارنة مع Marp CLI، فحص الترخيص/التبعيات/الثغرات/النشاط، وتسجيل القرار في `docs/OSS_REUSE_REGISTER.md` + `THIRD_PARTY_NOTICES.md`.
- [ ] **اختبار معياري (benchmark) عربي/فرنسي حقيقي**: عرض من 6 شرائح فيه نقط، اقتباس، رسم بياني، جدول، تذييل، ومراجع، مع نص عربي RTL وفرنسي مختلط — والتحقق من **الاتجاه الفعلي** لا من الافتراض.
- [ ] خدمة توليد + نقاط API: `POST /api/v1/presentations/jobs`، حالة المهمة، `GET /api/v1/artifacts/{id}/download`، مع ملكية/عزل، حصص، وحذف يتبع الأثر.
- [ ] واجهة `/presentations` عربية RTL + مفاتيح fr، مع وسم "مسودة للمراجعة".
- [ ] اختبارات: سلامة ZIP، قابلية التحرير (عناصر قابلة للتعديل فعلاً)، فتح/عرض بـ LibreOffice إن توفر، حلّ كل اقتباس إلى مصدر محفوظ، منع وصول مستخدم لعرض مستخدم آخر.
- [ ] ADR جديد + تحديث `THREAT_MODEL.md` و`DATA_POLICY.md`.

### P2 — Phase 7: الإدارة والنشر (**جزئي**)

- [ ] **دور admin منفصل** وصلاحيات، ونقاط إدارة، و**أحداث تدقيق (audit events)** مُخفاة الهوية — غير موجودة حالياً.
- [ ] مراقبة/قياس: مقاييس الاستخدام، لوحة الحدود، تنبيهات، سجلات مُطهَّرة من محتوى المستندات (السجلات الحالية جيدة لكن لا توجد مقاييس/تنبيهات).
- [ ] **نشر حقيقي مُختبَر**: الواجهة لا تملك `Dockerfile` ولا إعداد نشر (مثلاً Vercel)؛ اختيار هدف، ضبط `SESSION_SECRET`/`COOKIE_SECURE`/`ALLOWED_ORIGINS`، PostgreSQL مُدارة وتخزين خاص، TLS، حدود الذاكرة/البدء البارد، **اختبار الرابط العام فعلياً** (ليس ادعاءً)، خطة rollback، وتجربة نسخ/استعادة على البنية الحقيقية.
- [ ] تحديث `docs/DEPLOYMENT.md` و`docs/STAGING_ACCEPTANCE_REPORT.md` بنتائج النشر الحقيقية.

### P3 — فجوات وقيود: إمّا تُغلق أو يُعلَن قبولها رسمياً

| # | الفجوة | الأثر | التوصية |
|---|---|---|---|
| 1 | **لا OCR** للـ PDF الممسوح ضوئياً | كبير في السياق الجامعي الجزائري | إما إضافة OCR (tesseract + باقات ar/fr) مع benchmark، أو إبقاء القرار موثّقاً في ADR 0003 |
| 2 | **لا يوجد mock AI gateway** — `ENABLE_AI_GATEWAY` مجرد علم في `config.py` (لا يوجد مجلد `ai_gateway`) | معيار خروج Phase 1 غير مكتمل؛ كل الإجابات "انتقاء مقاطع" لا توليد | بناء واجهة مزوّد + mock حتمي، أو تعديل خطة المشروع لإسقاط المطلب مع توضيح أن المسار استخراجي فقط |
| 3 | **لا تصدير لبيانات الحساب** (يوجد حذف فقط) | معيار خروج Phase 2 ناقص | إضافة تصدير (JSON/حزمة) مع اختبار حذف/تصدير |
| 4 | **استرجاع المصادر الحية غير موثّق** (JORADP/ASJP/SNDL) | Gate C أثبت DNS/TCP/TLS فقط، لا المحتوى ولا الاقتباس ولا شروط الاستخدام | اختبار end-to-end حقيقي + مراجعة شروط كل مصدر وسياسة allowlist |
| 5 | **محرك مكافحة الفيروسات لا يُشحن** | الكشف موثّق في CI فقط؛ على المشغّل توفير `clamd` | تثبيته في بيئة النشر وإعادة Gate A هناك قبل أي ادعاء حماية |
| 6 | **Grounding لغوي معجمي**: `claim_support_honest` = 97.4% على v2 مع 5 حالات فاشلة معروفة | احتمال تقديم اقتباس لا يدعم الادعاء | إبقاء وسم "أدلة غير كافية" في الواجهة، وتحسين كشف التناقض/الأسئلة القطبية |
| 7 | **لا تشفير at rest**، ولا اختبار اختراق/ـ fuzzing | مخاطرة أمنية مقبولة حالياً لكنها غير مغلقة | توثيق القرار أو إضافة تشفير + مراجعة خارجية قبل الإنتاج |
| 8 | `braces` (5 ثغرات high، dev-only، بلا إصدار مُصحَّح) | مقبول وموثّق | إعادة الفحص عند صدور إصلاح من المشروع الأم |
| 9 | **عامل المعالجة لكل عملية** + قيود التوسّع الأفقي | لا يمكن التشغيل متعدد النسخ بحرية | إما قاعدة بيانات/طابورة مشتركة أو إبقاء "نسخة واحدة" كقيد مُعلن |
| 10 | خلط عربي/لاتيني في استخراج PDF لا يعود مطابقاً دائماً | جودة النصوص المقتبسة | benchmark مخصّص + تحسين التطبيع |
| 11 | `docker compose up` **غير مُختبَر** (البناء والتشغيل المنفرد مُختبَران) | ادعاء نشر ناقص | تشغيل الـ stack كاملاً في runner ودوّن النتيجة |
| 12 | لا Docling ولا benchmark للمسحات/الجداول (انحراف معلن عن Phase 3) | توافق مع الخطة الأصلية | إبقاء ADR يشرح سبب الاستبدال بـ pypdf/python-docx |

### P4 — نظافة الإصدار

- تحديث `README.md` الرئيسي (على `main` حالياً يقول "لا توجد أوامر تشغيل موثقة") ليصبح بعد الدمج مرجعاً واحداً صادقاً.
- إنشاء وسم/إصدار (مثلاً `v0.3.0` كما في `package.json`) بعد الدمج + Phase 6.
- قائمة فحص "تعريف الإنجاز" أدناه.

---

## 7. تعريف الإنجاز (Definition of Done)

- [ ] `main` يحتوي كامل عمل PR #1، وCI (4 jobs) أخضر على `main` نفسه.
- [ ] بوابات Staging A/B/C تعمل من `main` وخضراء على آخر commit.
- [ ] Phase 6 منفّذة ومختبرة (PPTX قابل للتحرير، RTL عربي/فرنسي، اقتباسات محلولة).
- [ ] Phase 7 منفّذة: دور admin، تدقيق، حدود استخدام، مراقبة، **نشر حقيقي مُختبَر عبر رابط فعلي** + تجربة استعادة + دليل rollback.
- [ ] الوثائق متسقة (لا تناقضات القسم 5)، ومعها `LICENSE` و`SECURITY.md` وإشعارات الترخيص محدّثة.
- [ ] القيود المتبقية (OCR، AI، المصادر الحية، الفيروسات، التشفير...) إمّا مغلقة أو **مقبولة صراحةً ومكتوبة** في `THREAT_MODEL.md` و`ROADMAP.md`.
- [ ] لا أسرار في Git، وكل ادعاء "اجتاز" مقرون بأمر ونتيجة وexit code.

**تقدير نوعي (بدون وعود زمنية):** العمل المتبقّي الرئيسي = P0 (سريع، ساعات عمل)، ثم P1 Phase 6 (مرحلة كاملة)، ثم P2 Phase 7 (مرحلة كاملة + بنية تحتية)، ثم P3 حسب القرار. **لا شيء من P1/P2 بدأ بعد.**

---

## 8. أدلة التحقق (ما استخدمته فعلاً)

```
# محلي
git status / git log --all --oneline / git ls-files / find / wc -l / du -sh
git ls-remote --heads origin
git fetch origin arena/1cb7b9d2-mafhum:refs/remotes/origin/phase3     # بدون تبديل فرع
git fetch origin arena/dee89266-mafhum:refs/remotes/origin/other       # بدون تبديل فرع
git show refs/remotes/origin/phase3:<file>                              # قراءة كل الوثائق والـ manifests
git ls-tree -r --name-only refs/remotes/origin/phase3                   # 174 ملفاً

# GitHub (معلومات عامة فقط)
gh repo view Devmraustro/MAFHUM --json ...        # licenseInfo = null، isEmpty = false
gh pr view 1 / gh pr checks 1 / gh pr list        # PR وحيد، مفتوح، checks خضراء
gh run view 40397915589 ...                       # CI: 4/4 success على 2719689
gh run view 40377229156 ...                       # Gates A/B/C: success على 3589dc8
gh run list --workflow=staging-acceptance.yml     # تاريخ المحاولات الفاشلة ثم النجاح
gh issue list / gh release list                   # فارغان
```

**ما لم أتحقق منه:** إعدادات حماية الفرع `main` (403)، أي تشغيل محلي للاختبارات في هذه الجلسة، وأي نشر فعلي على بنية تحتية (لا يوجد أصلاً).

---

## 9. الخطوة التالية المقترحة

الأولوية القصوى: **دمج PR #1 بعد تحديث وصفه** → مزامنة فرع الجلسة → إصلاح محفّز بوابات staging → توحيد الوثائق → ثم البدء بـ Phase 6 (PPTX).

> النسخة الأصلية من هذا التقرير كُتبت قبل تنفيذ P0. انظر قسم "تحديث التنفيذ" في الأسفل.

---

## 10. تحديث التنفيذ — P0 نُفّذ (نفس اليوم)

بعد موافقة المستخدم، نُفّذت عناصر P0 فعلياً على الفرع `arena/71aa7382-mafhum`:

| العنصر | ما نُفّذ |
|---|---|
| وصف PR #1 | حُدّث عبر `gh api PATCH .../pulls/1` (تعذّر `gh pr edit` بخطأ Projects-classic GraphQL) ليصف المراحل 1–5 والقيود الحقيقية |
| الدمج | `gh pr merge 1 --merge` → `MERGED` في 2026-10-27T11:47:19Z، merge commit `8ed788d` على `main` |
| المزامنة | `git fetch origin main` + `git merge origin/main` على فرع الجلسة (بقي 175 ملفاً متتبَّعاً) + `git push origin arena/71aa7382-mafhum` |
| محفّز البوابات | `.github/workflows/staging-acceptance.yml` صار يعمل على `push: branches: [main]` بدل اسم الفرع القديم؛ `workflow_dispatch` أصبح متاحاً من الفرع الافتراضي |
| توحيد الوثائق | `README.md`، `docs/ROADMAP.md`، `docs/THREAT_MODEL.md`، `docs/SECURITY_ASSESSMENTS.md`، `docs/STAGING_ACCEPTANCE_REPORT.md` صُحّحت تناقضاتها (الحصص/الـ worker، Docker، فحص الفيروسات، حالة البوابات) |
| `.env.example` | أُضيفت المتغيرات الحقيقية الناقصة (`PROCESSING_WORKER_ENABLED`، `CLAMD_*`، `MALWARE_SCAN_REQUIRED`، حدود الـ throttle) وصُحّح تعليق الـ process-local |

**ما لم يُنفّذ بعد من P0:** إضافة `LICENSE` و`SECURITY.md` و`CODEOWNERS` (تحتاج قرار ترخيص من المالك)، أرشفة الفرع القديم `arena/dee89266-mafhum` (يحتاج موافقة صريحة قبل الحذف)، وإعادة تشغيل بوابات A/B/C على `main` بعد الدمج. Phase 6 تُنفّذ في نفس الفرع في العمل التالي.

---

## 11. تحديث التنفيذ — Phase 6 نُفّذت (نفس اليوم)

### ما أُنجز

| البند | التفصيل |
|---|---|
| المولّد | `DeckGenerator` بروتوكول + `PythonPptxGenerator` (python-pptx 1.0.2, MIT). كتابة RTL على مستوى الفقرة (`a:pPr@rtl`)، التشغيل (`a:rPr@lang`, `a:cs`) والجدول (`a:tblPr@rtl`) |
| المقارنة | PptxGenJS 4.0.1 (MIT) في `apps/slides` على **نفس** مواصفة العرض المعيارية؛ Marp مرفوض (تصدير صوري/تجريبي). ADR 0005 + `docs/PPTX_BENCHMARK.md` بأرقام مقاسة |
| المحتوى | العرض يُبنى من اقتباسات حرفية فقط + مخطط بعدّ الاقتباسات الحقيقي + جدول مراجع；لا نصوص أكاديمية مُختلقة |
| قاعدة البيانات | هجرة `0008_presentations` بجدولين `presentation_jobs` و`presentation_artifacts` مع قيود وفهارس، واختبار linear-chain محدّث |
| API | `POST/GET /api/v1/presentations/jobs`، `GET/DELETE /api/v1/presentations/jobs/{id}`، `GET /api/v1/artifacts/{id}/download` — ملكية مُتحقَّق منها في SQL، و404 متطابقة للمستخدم الآخر |
| الأمن | حدود على عدد العروض/الاقتباسات/الشرائح/النص/حجم المخرج؛ حذف المهمة يمسح الـ blob؛ حذف الحساب يمسح عروض PPTX قبل حذف الصفوف؛ إعادة حساب توفّر المصادر عند القراءة |
| الواجهة | صفحة `/presentations` عربية RTL + فرنسية، وسم "مسودة" دائم، تنزيل نسبي، تحذير عند حذف مصدر، 6 اختبارات جديدة |
| CI | خطوة لاختبارات مولّد المقارنة + خطوة `npm audit` له + خطوة benchmark لكلا المولّدين |

### نتائج التحقق الفعلية (محلياً، 2026-10-27)

```
ruff check apps/api                      All checks passed
ruff format --check apps/api            116 files already formatted
mypy src (strict)                       Success: no issues found in 66 source files
pytest (full, real PostgreSQL)          all passed, 4 skips (live-egress opt-ins), exit 0
pip-audit                               No known vulnerabilities found (exit 0)
web: typecheck / lint / test / build    0 / 0 / 107 passed / OK (route /presentations)
web: npm audit (prod + full)            0 vulnerabilities
apps/slides: npm test                   2 passed
apps/slides: npm audit                  0 vulnerabilities (after image-size 2.0.4 override)
pptx benchmark (both generators)        exit 0, 6 slides, editable, RTL, chart+table
YAML + .env.example contract            OK / 0 unknown settings
```

**دليل CI إضافي (على GitHub runner):** فُتح **PR #2** من فرع الجلسة إلى `main` ويحمل كل عمل P0 + Phase 6. تشغيل `MAFHUM CI` رقم **`40518074098`** على HEAD `ee2fe8ac` انتهى **success بـ 4/4 وظائف**: Repository baseline، Backend (lint/types/tests)، Migrations (upgrade/downgrade/upgrade)، Frontend (lint/types/tests/build + اختبارات مولّد المقارنة + `npm audit` له + benchmark لكلا المولّدين). هذا يثبت أن مسار Node/PptxGenJS يعمل على runner حقيقي وليس فقط محلياً.

**ملاحظة بيئية بصراحة:** لا يوجد Docker ولا LibreOffice/PowerPoint في هذه البيئة؛ لذلك **لم يُفتح أي عرض بصرياً** ولم يُبنى `docker compose up`. محاولة `workflow_dispatch` يدوي عبر API رُفضت بـ 403 لضعف صلاحيات الـ token، لذا بوابات staging لم تُعَد بعد الدمج. سجلات خطوات الـ runner غير قابلة للتنزيل من هذه البيئة (blob host خارج المسموح)، لكن حالة كل خطوة موثّقة كـ success في واجهة التشغيل.

### ما تبقّى بعد Phase 6 (محدَّث)

1. **Phase 7** — دور admin وأحداث تدقيق، مراقبة/تنبيهات，**نشر حقيقي مُختبَر عبر رابط فعلي**، تجربة نسخ/استعادة ودليل rollback. الواجهة لا تزال بلا `Dockerfile`/إعداد نشر.
2. **دمج PR #2** (هذا الفرع) في `main` حتى تدخل إصلاحات P0 والـ Phase 6 إلى الفرع الافتراضي.
3. **إعادة بوابات Staging A/B/C** على `main` بعد الدمج، وتشغيل `docker compose up` فعلياً في runner.
4. **فحص بصري للعروض** في PowerPoint/LibreOffice لتأكيد تشكيل العربية واتجاه الجداول.
5. **الفجوات القديمة ما زالت قائمة:** لا OCR، لا mock AI gateway (العلم فقط)، لا تصدير بيانات الحساب، استرجاع المصادر الحية غير موثّق، لا محرك فيروسات في التثبيت الافتراضي، لا تشفير at rest، ولا اختبار اختراق.
6. **إصدار** `LICENSE`/`SECURITY.md`/`CODEOWNERS` (يحتاج قرار المالك) وأرشفة الفرع القديم `arena/dee89266-mafhum`.

---

## 12. تحديث التنفيذ — Phase 7 نُفّذت (نفس اليوم)

بعد موافقة المستخدم **دُمج PR #2** في `main` (14:38 UTC، commit `a4b90d1`)، وزُومن فرع الجلسة، ثم نُفّذت Phase 7.

### ما أُنجز

| البند | التفصيل |
|---|---|
| دور admin | هجرة `0009_admin_audit` مع `CHECK (role IN ('student','admin'))`؛ المنح **من CLI فقط** (`apps/api/tools/set_admin.py`) ولا يوجد أي endpoint لتغيير الصلاحية |
| أحداث التدقيق | جدول `audit_events` append-only، و`actor_id` بـ `ON DELETE SET NULL`؛ تسجيل التسجيل/الدخول/الفشل/الخروج/الحذف/الإنشاء/رفض الوصول/عرض الأدمن؛ `details` بقائمة مسموحات بدون أي بيانات شخصية |
| API الإدارة | `/api/v1/admin/overview` و`/usage` و`/audit-events` و`/metrics` (عدادات بصيغة Prometheus بدون PII) — للقراءة فقط، وغير الأدمن يحصل على 404 مطابقة |
| صورة الويب | `apps/web/Dockerfile` متعدد المراحل، مستخدم غير جذري، تبعيات إنتاج فقط، فحص صحة بـ Node؛ `apps/web/.dockerignore` و`apps/web/vercel.json` وخدمة `web` في Compose |
| **تجربة نشر حقيقية** | سير عمل `.github/workflows/deployment-rehearsal.yml` بنى وشغّل db+api+web على runner فيه Docker، وأجرى رحلة HTTP كاملة، ثم **نسخة احتياطية هادئة + استعادة في قاعدة مؤقتة مع تحقق checksums** |
| النشر على Vercel | مشروع `mafhum-web` ونشر `dpl_Hhpr51CPn24fZT91yisV78S4i6oy` بحالة READY على https://mafhum-web.vercel.app/ — الصفحة العربية RTL تُخدم فعلياً (تحقق HTTP) |
| الوثائق | `docs/DEPLOYMENT_RUNBOOK.md`، `docs/ROLLBACK_GUIDE.md`، `docs/DEPLOYMENT_REHEARSAL_REPORT.md`، `SECURITY.md`، `.github/CODEOWNERS`، وتحديث THREAT_MODEL/DATA_POLICY/ROADMAP/README |

### أدلة التشغيل الفعلية

```
MAFHUM CI على PR #3               40530009023 / 40527952019 / 40528767578 → 4/4 خضراء
Staging A/B/C على main           40523074856 → success (بعد دمج PR #2 تلقائياً)
Deployment rehearsal            40530008866 → success (10/10 خطوات، 1m48s)
Vercel web deployment           READY/PROMOTED، 31.8s، GET / → 200 عربي RTL
                               GET /health/live → 502 DNS_HOSTNAME_NOT_FOUND (متوقع: لا يوجد API هناك)
pytest كامل (PostgreSQL حقيقي)  كل الاختبارات ناجحة + 4 skips معلنة
ruff + mypy strict               نظيفان (69 ملف مصدر)
```

**ثلاثة أخطاء حقيقية اكتُشفت وأُصلحت أثناء التجربة:** انتقال `ubuntu-latest` إلى Ubuntu 26 (تم تثبيت الـ runner على 24.04)، و`rewrites` في Next تُبنى وقت البناء لا التشغيل (أُضيف `ARG` + حارس في `vercel.json`)، ونطاق `.invalid` مرفوض من مدقق البريد (استُبدل بـ `example.org`). التفاصيل في `docs/DEPLOYMENT_REHEARSAL_REPORT.md`.

### ما تبقّى حتى الإنجاز الكامل (محدَّث)

1. ~~**دمج PR #3** في `main`~~ — **تم** في 2026-10-27 21:54 UTC (commit `e106727`) بعد المراجعة؛ أطلق الدمج تلقائياً CI + Staging + Deployment rehearsal على `main`.
2. **استضافة الـ API فعلياً**: لا يوجد أصل عام للـ API، لذلك نشر Vercel الحالي **واجهة فقط** وكل طلب `/api` يفشل. يحتاج خدمة FastAPI + PostgreSQL مُدارة + TLS + `ALLOWED_ORIGINS` صحيح + إعادة بناء الويب بـ `MAFHUM_API_URL` حقيقي.
3. **ربط المراقبة**: لا يوجد تنبيه/لوحة/تجميع سجلات/فحص جاهزية خارجي؛ endpoint الـ metrics مصدر فقط.
4. **فحص بصري للعروض** في PowerPoint/LibreOffice (غير متوفرين في البيئة).
5. **اختيار ترخيص** وإضافة `LICENSE` (المستودع بلا ترخيص — قرار المالك).
6. **الفجوات القديمة**: لا OCR، لا mock AI gateway، لا تصدير حساب، مصادر حية غير موثّقة، لا محرك فيروسات افتراضي، لا تشفير at rest، لا اختبار اختراق/تغميم، ولا أرشفة للفرع القديم `arena/dee89266-mafhum`.
7. ~~**إعادة بوابات Staging** بعد دمج PR #3~~ — **تمت تلقائياً** (run `40590637500`). `workflow_dispatch` اليدوي ما زال مرفوضاً بـ 403 لصلاحيات الـ token، لكن محفّز `push` على `main` يعمل.


---

## 13. تحديث ما بعد الدمج — إكمال الخطوات (نفس اليوم، على الفرع `arena/1c68522d-mafhum`)

بعد ضغط **Continue** انتقل العمل إلى فرع جديد، ودُمج PR #3 فعلياً (لم يكن مدموجاً رغم حالة `pr_merged` السابقة)، ثم نُفّذ ما يلي.

### أ. تصحيح تناقضات الوثائق الخمسة

| الملف | التصحيح |
|---|---|
| `docs/SECURITY_ASSESSMENTS.md` §2 | حُذفت عبارة "لا يوجد `apps/web/Dockerfile` ولم يُشغّل Compose"؛ أُضيف دليل run `40530008866` و`40523074856` مع ما زال غير مُتحقَّق (فحص الأسرار في الصور، نشر دائم) |
| `docs/ROADMAP.md` §Phase 7 | حُذفت عبارة "بوابات Staging لم تُعَد" والتحفّظ "if recorded"؛ أُضيف قسم ما بعد الدمج |
| `apps/web/Dockerfile` | الترويسة صارت "VERIFIED ON CI (2026-10-27)" بدل "NOT YET VERIFIED" |
| `docker-compose.yml` | نفس التصحيح مع تمييز واضح: rehearsal على CI ≠ خدمة إنتاج |
| `docs/STAGING_ACCEPTANCE_REPORT.md` §5 | وُسم القسم كسجل تاريخي superseded يعلو دليل 2026-10-27 |
| `docs/STATUS_REPORT_2026-10-27.md` §12 | صُحّحت عبارتا "لم أدمجه" و"بعد الدمج" |

### ب. تحسينات أمنية (مراجعة ما بعد الدمج)

- `AuditService.record_denied_once()`: حدّ أقصى **صف رفض واحد لكل مستخدم كل 60 ثانية** عبر `COUNT` في قاعدة البيانات (يعمل عبر النسخ المتعددة) — منع إغراق جدول التدقيق.
- `/api/v1/admin/metrics` صار يسجّل حدث `admin_metrics_viewed` أسوة بباقي نقاط الإدارة.
- `apps/api/tools/set_admin.py` يرفض تجريد **آخر admin نشط** بدون `--force` (ودائماً يسجّل الحدث).
- `tools/e2e/compose_smoke.py` صار يتحقق من جسم الاستجابة: عند 201 يتأكد من وجود artifact و`slide_count>0`؛ عند 409 يوضّح أن `insufficient_evidence` إجابة صحيحة غير مُختلقة.
- 3 اختبارات جديدة في `test_admin_audit.py` (المجموع 14 في الملف) وكلها ناجحة.

### ج. مراقبة مجانية حقيقية

- `tools/monitoring/probe.py`: فحص uptime/mحتوى بلا تبعيات (stdlib فقط) — يتحقق 200 + وجود `مَفْهُوم` + مهلة زمنية، ويُخرج JSON مُطهَّراً.
- `.github/workflows/uptime-monitor.yml`: مهمة `selftest` على كل push/PR (صفحة سليمة تنجح، صفحة خاطئة تفشل → لا أخضر كاذب)، ومهمة `live-probe` كل 15 دقيقة + `workflow_dispatch` على نطاق Vercel مع `vars.MAFHUM_WEB_URL` قابل للتغيير. قناة التنبيه = إشعار فشل GitHub Actions.
- `docs/MONITORING.md` يوثّق بصراحة: لا يوجد بعد scrape credential لـ Prometheus، ولا لوحة، ولا تنبيه خارجي، ولا مراقبة للـ API لأنه غير منشور.

### د. فحص ما قبل العرض البصري

- `apps/api/tools/check_pptx_visual.py`: يفحص ZIP/OOXML، absence of bidi controls، علامات RTL، `a:cs`، `lang`، أجزاء chart/table، أبعاد 16:9، وتقدير فيض النص — ويُعلن صراحة `render_check: NOT PERFORMED`.
- أُضيف إلى `MAFHUM CI` خطوة على **كلا** ملفي الـ benchmark. محلياً: كلا الملفين `ok=true, problems=[], warnings=[]`.

### هـ. مسار نشر كامل (تجهيز، لا تنفيذ)

- `deploy/docker-compose.prod.yml` + `deploy/Caddyfile` + `deploy/.env.prod.example`: db + api + web + Caddy مع TLS تلقائي، وحدات تخزين دائمة، `APP_ENV=production`، وأسرار من البيئة فقط. **لم يُنفَّذ** (لا خادم/نطاق/بيانات دخول).
- `docs/FULL_STACK_DEPLOYMENT.md`: يشرح لماذا يبقى نشر Vercel واجهة فقط، ولماذا يحتاج المشروع قرصاً دائماً (لا يوجد object-store adapter)، وخطوات المشغّل.
- **قرار المستخدم:** الإبقاء على نشر Vercel التجريبي **كما هو**؛ لم أغيّره.
- **قرار المستخدم:** **لا ملف `LICENSE` الآن** — يبقى المستودع بلا ترخيص، موثّق كقرار مفتوح للمالك.

### و. أدلة `main` بعد دمج PR #3 (محفّز push تلقائي)

```
40590637390  MAFHUM CI                      success (4/4 وظائف)
40590637423  Deployment rehearsal           success (db+api+web + نسخ/استعادة)
40590637500  Staging acceptance probes      success (Gate A/B/C)
```

### ز. ما تبقّى فعلياً (بعد هذا التحديث)

1. توفير خادم/نطاق وتشغيل `deploy/` ثم ضبط `MAFHUM_API_URL` وقت البناء و`ALLOWED_ORIGINS` — عندها فقط يصبح `/api` عاملاً ويمكن تغيير نشر Vercel (الحالي مُبقى عليه بقرارك).
2. إضافة scrape credential مقيّد لـ `/admin/metrics` + قواعد تنبيه + لوحة، وتحديد مدة الاحتفاظ بـ `audit_events` والأرشيفات.
3. فحص بصري بشري لملف PPTX في PowerPoint/LibreOffice (لا يتوفران في أي بيئة تحقق هنا).
4. اختيار ترخيص لاحقاً، أرشفة الفرع القديم `arena/dee89266-mafhum`، والفجوات القديمة (OCR، mock AI، تصدير الحساب، تشفير at rest، اختبار اختراق).

---

## 14. الخاتمة التنفيذية — حالة `main` بعد كل عمليات الدمج (2026-10-27 22:17 UTC)

| العنصر | الحالة |
|---|---|
| PR #1 | `MERGED` → `8ed788d` (المراحل 1–5 + P0) |
| PR #2 | `MERGED` → `a4b90d1` (Phase 6 + توحيد الوثائق) |
| PR #3 | `MERGED` → `e106727` (Phase 7: admin/تدقيق/نشر تجريبي) — **تم بعد المراجعة** |
| PR #4 | `MERGED` → `fa14bd1` (إصلاح التناقضات، تحسينات أمنية، مراقبة، pre-flight، مخطط النشر) |
| الفرع الحالي | `arena/1c68522d-mafhum` = `main` (نظيف، لا تغييرات غير ملتزم بها) |

### أدلة التشغيلات على `main` (كلها success)

```
40590637390  MAFHUM CI                      4/4 وظائف   (بعد PR #3)
40590637423  Deployment rehearsal           db+api+web + نسخ/استعادة
40590637500  Staging acceptance probes      Gate A/B/C
40593579789  Staging acceptance probes      Gate A/B/C   (بعد PR #4)
40593579832  MAFHUM CI                      4/4 وظائف   (بعد PR #4)
40593579965  Deployment rehearsal           db+api+web + نسخ/استعادة (بعد PR #4)
40593579909  Uptime monitor                 selftest + live-probe
```

### ما تحقق فعلياً في هذه الجولة

- **المراجعة:** لا أخطاء وظيفية مانعة؛ `ruff`/`mypy`/pytest كامل/107 اختبار واجهة/build/فحوص بنchmark كلها خضراء محلياً وفي CI.
- **التناقضات الوثائقية الخمسة** صُحّحت، وترويسات Docker/Compose صارت تعكس التحقق الحقيقي على CI.
- **التحسينات الأمنية:** تحديد رفض الوصول بصف/دقيقة/مستخدم، تدقيق `/metrics`، حماية آخر admin، وفحص أدق لاستجابة العروض في الـ smoke — مع 3 اختبارات جديدة.
- **مراقبة مجانية فعّالة:** `uptime-monitor.yml` يعمل كل 15 دقيقة؛ `selftest` يثبت أن الفاحص يرفض الصفحة الخاطئة؛ و`live-probe` نجح على نطاق Vercel.
- **فحص PPTX قبل العرض:** مضاف إلى CI على ملفي الـ benchmark، وكلاهما `ok=true` محلياً؛ ويُعلن صراحة أن الفحص البصري بالبكسل **لم يُجرَ**.
- **مخطط نشر كامل:** `deploy/` بمتغيراته التسعة كلها موثّقة في `.env.prod.example` (فحص اتساق: 0 ناقص، 0 زائد). **لم يُنفَّذ** لعدم توفر خادم/نطاق/بيانات دخول.
- **قراراك نُفّذا:** نشر Vercel التجريبي **بقي كما هو** (تحقق HTTP: `GET /` = 200، صفحة عربية RTL)، و**لا ملف `LICENSE`** حالياً (المستودع بلا ترخيص، موثّق كقرار مفتوح).

### ما لا يزال يحتاج **المالك/المشغّل** (لا يمكن إنجازه من هذه البيئة)

1. خادم + نطاق + تشغيل `deploy/`، ثم ضبط `MAFHUM_API_URL` وقت البناء و`ALLOWED_ORIGINS` — عندها فقط يعمل `/api` في نشر Vercel (الحالي واجهة فقط بقرارك).
2. بيانات دخول مقيدة لـ Prometheus + قواعد تنبيه + لوحة + سياسة احتفاظ بـ `audit_events` والأرشيفات.
3. فتح ملف PPTX في PowerPoint/LibreOffice للتحقق البصري النهائي.
4. اختيار ترخيص لاحقاً، أرشفة الفرع القديم `arena/dee89266-mafhum`، والفجوات القديمة (OCR، mock AI، تصدير الحساب، تشفير at rest، اختبار اختراق).


---

## 15. تحديث 2026-10-28 — تصحيح السجل التاريخي وتنفيذ المهام المتبقية

> هذا القسم هو **المرجع الحالي**. كل ما قبله سجلّ زمني لحالة 2026-10-27 ولا يجوز
> قراءته كحالة راهنة.

### 15.1 تصحيحات صريحة

| العبارة القديمة | التصحيح الموثّق |
|---|---|
| §5: «`docker compose up` تحديداً لم يُختبَر» | **خاطئ الآن.** شغّل `deployment-rehearsal.yml` الـ stack كاملاً على runner: `40530008866` ثم `40532185782` و`40590637423` و`40593579965` — كلها success، وتتضمن رحلة HTTP حقيقية ونسخاً/استعادة بتحقق SHA-256. |
| §5: PR #1 مفتوح | دُمج `8ed788d`، ثم PR #2 `a4b90d1` و#3 `e106727` و#4 `fa14bd1` و#5 `2a15f0c`. |
| §6/§11: لا يوجد ClamAV في الإنتاج، ولا تصدير حساب، ولا سياسة احتفاظ، ولا تشفير at rest، ولا رمز Prometheus | أُضيفت خدمة `clamd` حقيقية في `deploy/` مع فحص صحة قائم على SCAN، وتصدير حساب مقيّد، واحتفاظ اختياري محدود، و**تصميم** تشفير فقط (غير مُفعَّل)، ورمز scrape مقيّد النطاق مع قواعد تنبيه. التفاصيل في §15.2. |
| §9: لا يوجد فحص بصري للعروض | أُضيفت بوابة CI بـ LibreOffice headless + poppler للتحقق من عدد الصفحات وعدم فراغها ووجود العربية؛ يبقى فحص PowerPoint على Windows يدوياً. |

### 15.2 ما نُفّذ في فرع المراجعة (2026-10-28)

1. **فحص فيروسات إنتاجي fail-closed:** `deploy/clamd/` (صورة Debian + ClamAV)،
   إعداد TCP فقط، bootstrap بـ `freshclam`، تحديث كل 12 ساعة، فحص صحة ينفّذ
   `clamdscan` فعلياً. `deploy/docker-compose.prod.yml` يربط `api` بـ
   `clamd: service_healthy` ويثبّت `MALWARE_SCAN_REQUIRED=true`.
   `assert_production_ready()` ترفض الإقلاع بدونهما أو بدون عنوان daemon.
   `/health/ready` يضيف `malware_scanner`. بوابة
   `production-malware-acceptance.yml` تختبر نظيف / EICAR / انقطاع المحرك بمحرك حقيقي.
2. **تصدير بيانات الحساب:** `GET /api/v1/me/export` — ZIP متدفّق بحدود عدد/حجم/زمن،
   عزل ملكية في SQL، أسماء أعضاء آمنة (UUID + امتداد مسموح)، تدقيق `account_exported`.
3. **الاحتفاظ بسجل التدقيق:** `AUDIT_RETENTION_DAYS` (0 = بلا حذف)،
   `AuditService.prune` بدفعات محدودة ومعاملات مستقلة، وأمر
   `purge_audit.py` جاف افتراضياً ويحتاج `--apply`. السياسة موثّقة في
   `docs/DATA_POLICY.md` §8.
4. **تشفير at rest:** `docs/ADR/0006-encryption-at-rest.md` + واجهة
   `FieldEncryptor` وتنفيذ AES-256-GCM مع AAD وتدوير مفاتيح بمفاتيح اختبار
   صناعية فقط. **غير مُفعَّل في الإنتاج**؛ لا يوجد مفتاح إنتاجي ولا KMS.
5. **مراقبة مقيّدة النطاق:** `METRICS_SCRAPE_TOKEN` (>= 32 حرفاً، إلزامي في
   الإنتاج)، صلاحية لـ `/admin/metrics` فقط، مقاييس جديدة،
   `deploy/prometheus/*` و`deploy/docker-compose.monitoring.yml`. **لا وجهة تنبيه
   مُهيّأة بعد** — تحتاج قرار المالك.
6. **بوابة أمنية في CI:** Bandit + `tools/security_baseline.py` +
   `tools/acceptance/prod_compose_contract.py` + `docs/PENTEST_READINESS.md`.
   اختبار اختراق فعلي **لم يُجرَ**.
7. **عرض PPTX حقيقي:** `pptx-render.yml` + `check_pptx_render.py` مع فحص صفحات
   فارغة وحروف عربية و U+FFFD. هذا دليل LibreOffice، **ليس** تطابق PowerPoint.
8. **Windows:** إصلاح بناء URL في اختبارات النسخ/الاستعادة عبر `make_url`،
   اختبارات قابلية النقل، و`docs/WINDOWS_SETUP.md`. التنفيذ على Windows نفسه
   **مازال مطلوباً** في بيئة المالك.
9. **وثائق:** تصحيح `PROJECT_ARCHITECTURE.md` و`SECURITY_ASSESSMENTS.md`
   و`DATA_POLICY.md` و`README.md` و`ROADMAP.md` وشعارات «تاريخي» على التقارير القديمة.
10. **عقد نشر الإنتاج:** مدقّق ثابت + `docker compose config` في CI؛ تحديث
    `docs/DEPLOYMENT_RUNBOOK.md` و`docs/FULL_STACK_DEPLOYMENT.md` بالمتطلبات الدقيقة.

### 15.3 ما لم يتحقق بعد (لا تُعلَن نهايته)

- **لا يوجد منفذ API عام.** موقع Vercel بقي واجهة فقط بقرار المالك.
- **لا نشر إنتاجي ولا نطاق ولا DNS ولا بيانات دخول حقيقية.**
- **التشفير at rest غير مُفعَّل** وKMS غير محدد.
- **وجهة التنبيه غير مهيّأة**؛ لا يوجد dashboard ولا اختبار توصيل تنبيه.
- **لا اختبار اختراق/DAST** ولا فحص حاويات على صور الإنتاج.
- **فحص PowerPoint على Windows وتشغيل pytest على Windows** لم يُجريا في هذه البيئة.
- OCR وبوابة AI واسترجاع المصادر الحي ما زالت خارج النطاق وبحاجة قرار مالك.

**الحالة النهائية: غير مكتمل (Incomplete).**
