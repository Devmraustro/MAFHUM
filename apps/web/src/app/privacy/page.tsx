"use client";
import { useLocales } from "@/components/locale-provider";

export default function Privacy() {
  const { dictionary } = useLocales();
  const { integrity } = dictionary;

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold navy-text">سياسة البيانات والخصوصية (مسودة)</h1>
      <p className="text-navy-700 leading-relaxed">
        هذه الصفحة ملخص تنفيذي لسياسة بيانات MAFHUM. التفاصيل الكاملة في{" "}
        <code className="rounded bg-navy-50 px-1.5 py-0.5 text-sm font-mono" dir="ltr">
          docs/DATA_POLICY.md
        </code>{" "}
        في المستودع. المستند ليس استشارة قانونية ولا شهادة مطابقة.
      </p>

      <section className="space-y-4 border border-navy-100 rounded-lg p-6 bg-navy-50">
        <h2 className="font-bold navy-text mb-2">ما الذي نجمعه ولماذا</h2>
        <ul className="list-disc ps-6 text-navy-700 leading-relaxed space-y-2">
          <li>بيانات الحساب الأساسية اللازمة للمصادقة وحفظ المشاريع المملوكة للطالب.</li>
          <li>المستندات التي يرفعها الطالب (في المراحل اللاحقة) تُخزّن خاصةً افتراضيًا مع ملكية صريحة.</li>
          <li>عدادات الحصص وسجلات منقحة دون محتوى المستندات أو كلمات المرور النصية.</li>
        </ul>
      </section>

      <section className="space-y-4 border border-navy-100 rounded-lg p-6 bg-navy-50">
        <h2 className="font-bold navy-text mb-2">الذكاء الاصطناعي ومعالجة البيانات</h2>
        <ul className="list-disc ps-6 text-navy-700 leading-relaxed space-y-2">
          <li>كل مخرجات الذكاء الاصطناعي مسودة تحتاج مراجعة الطالب.</li>
          <li>لا تُرسل الرفعات إلى مزودي الاستنتاج الخارجيين إلا عند الضبط والإفصاح الصريح.</li>
          <li>المزود الافتراضي للتطوير والاختبار هو مزود تجريبي (mock) محلي، لا يتطلب بيانات اعتماد ولا إنترنت.</li>
        </ul>
      </section>

      <section className="space-y-4 border border-navy-100 rounded-lg p-6 bg-navy-50">
        <h2 className="font-bold navy-text mb-2">حقوق الطالب وحدود الحذف والاحتفاظ</h2>
        <ul className="list-disc ps-6 text-navy-700 leading-relaxed space-y-2">
          <li>تصدير البيانات بصيغة JSON وحذف الحساب عبر المسار المحمي <code dir="ltr">/api/v1/me</code>.</li>
          <li>حذف الحساب يزيل فورًا سجل المستخدم وجميع جلساته النشطة والمشاريع المملوكة له من قاعدة البيانات الحية.</li>
          <li>
            تنبيه شفاف حول حدود الاحتفاظ: النسخ الاحتياطية المشفرة لقاعدة البيانات والسجلات التشغيلية المنقحة تخضع لدورات التدوير والاحتفاظ التشغيلية ولا يُعاد تحريرها بأثر رجعي، ولا يُدَّعى حذف بيانات لدى خدمات خارجية ما لم يُتحقق من ذلك فعليًا.
          </li>
        </ul>
      </section>

      <section className="border-t border-navy-200 pt-6 text-sm text-navy-600">
        <p>{integrity.items[3]}</p>
      </section>
    </div>
  );
}
