"use client";
import { useLocales } from "@/components/locale-provider";

export default function Status() {
  const { dictionary } = useLocales();
  const { status } = dictionary;

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold navy-text">{status.title}</h1>
      <p className="text-navy-700 leading-relaxed">{status.note}</p>

      <section className="border border-navy-200 rounded-lg overflow-hidden">
        <table className="w-full border-collapse text-navy-700 text-sm">
          <caption className="sr-only">{status.table.area} — {status.table.state}</caption>
          <thead className="bg-navy-50 border-b border-navy-200">
            <tr>
              <th className="text-right p-4 font-medium">{status.table.area}</th>
              <th className="text-right p-4 font-medium">{status.table.state}</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-navy-100">
            {status.rows.map((row, i) => (
              <tr key={i} className={i % 2 === 0 ? "bg-navy-50" : ""}>
                <td className="p-4">{row.area}</td>
                <td className="p-4">{row.state}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      <p className="mt-6 text-xs text-navy-500">
        لا تُعد أي ميزة مكتملة إلا بعد تنفيذها واجتياز بوابات الاختبار (lint، النوع، الاختبارات، البناء) مع نتائج فعلية مسجلة.
      </p>
    </div>
  );
}
