"use client";
import { useLocales } from "@/components/locale-provider";

export default function Privacy() {
  const { dictionary } = useLocales();
  const { integrity } = dictionary;

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold navy-text">سياسة البيانات والخصوصية (مسودة)</h1>
      <p className="text-navy-700 leading-relaxed">
        هذه الصفحة ملخص أولي لسياسة بيانات MAFHUM. التفاصيل الكاملة في
        <code className="rounded bg-navy-50 px-1.5 py-0.5 text-sm font-mono">docs/DATA_POLICY.md</code>
        في المستودع. المستند ليس استشارة قانونية ولا شهادة مطابقة.
      </p>

      <section className="space-y-4 border border-navy-100 rounded-lg p-6 bg-navy-50">
        <h2 className="font-bold navy-text mb-2">ما الذي نجمعه ولماذا</h2>
        <ul className="list-disc pl-6 text-navy-700 leading-relaxed space-y-2">
          <li>بيانات الحساب اللازمة للخدمات المحفوظة فقط عند تفعيل الحسابات (المرحلة 2).</li>
          <li>المستندات التي يرفعها الطالب وتُخزّن خاصةً افتراضيًا، مع سجل مصدر وحالة المعالجة.</li>
          <li>سجلات الاستخدام والإحصاءات دون محتوى المستندات، لأغراض الحصص والحدود فقط.</li>
        </ul>
      </section>

      <section className="space-y-4 border border-navy-100 rounded-lg p-6 bg-navy-50">
        <h2 className="font-bold navy-text mb-2">الذكاء الاصطناعي ومعالجة البيانات</h2>
        <ul className="list-disc pl-6 text-navy-700 leading-relaxed space-y-2">
          <li>كل مخرجات الذكاء الاصطناعي مسودة تحتاج مراجعة الطالب.</li>
          <li>لا تُرسل الرفعات إلى مزودي الاستنتاج externos إلا عند الضبط والإفصاح الصريح.</li>
          <li>المزود الافتراضي للتطوير والاختبار هو مزود تجريبي (mock) محلي، لا يتطلب بيانات اعتماد ولا الإنترنت.</li>
        </ul>
      </section>

      <section className="space-y-4 border border-navy-100 rounded-lg p-6 bg-navy-50">
        <h2 className="font-bold navy-text mb-2">حقوق الطالب</h2>
        <ul className="list-disc pl-6 text-navy-700 leading-relaxed space-y-2">
          <li>تصدير البيانات وحذف الحساب وفقًا لدورة حياة محددة (المرحلة 2).</li>
          <li>حذف الملفات الأصلية والنص المستخرج والمقتطفات والفهارس والتخزين المؤقت عند الحذف.</li>
          <li>عدم التدرّب على مستندات الطلاب افتراضيًا.</li>
        </ul>
      </section>

      <section className="border-t border-navy-200 pt-6 text-sm text-navy-600">
        <p>{integrity.items[3]}</p>
      </section>
    </div>
  );
}
