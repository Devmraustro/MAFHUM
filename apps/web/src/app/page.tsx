"use client";
import Link from "next/link";
import { useLocales } from "@/components/locale-provider";

export default function Home() {
  const { dictionary } = useLocales();
  const { hero, about, principles, stack, status, integrity } = dictionary;

  return (
    <div className="space-y-10">
      {/* Hero */}
      <section className="border border-navy-100 rounded-xl bg-navy-50 p-8 md:p-12 text-center md:text-right">
        <h1 className="text-3xl md:text-4xl font-bold navy-text tracking-tight mb-4">
          {hero.title}
        </h1>
        <p className="text-lg text-navy-700 mb-6 max-w-2xl mx-auto md:mr-auto">{hero.subtitle}</p>
        <div className="inline-flex items-center rounded-md border border-navy/20 bg-white px-4 py-3 text-sm font-medium text-navy-700 shadow-sm">
          <span className="mr-2 inline-block w-2 h-2 rounded-full bg-navy-600" aria-hidden="true" />
          {hero.status}
        </div>
        <p className="mt-4 text-sm text-navy-600 max-w-xl mx-auto md:mr-auto">{hero.statusDetail}</p>
        <Link
          href="/docs/PROJECT_ARCHITECTURE.md"
          className="mt-6 inline-block rounded-md border border-navy bg-navy px-5 py-2.5 text-sm font-semibold text-white hover:bg-navy-800 transition-colors"
        >
          {hero.readArchitecture}
        </Link>
      </section>

      {/* About */}
      <section className="space-y-6">
        <h2 className="text-xl font-bold navy-text">{about.title}</h2>
        <p className="text-navy-700 leading-relaxed">{about.intro}</p>
        <p className="text-navy-700 leading-relaxed">{about.mission}</p>
      </section>

      {/* Principles */}
      <section className="space-y-4">
        <h2 className="text-xl font-bold navy-text">{principles.title}</h2>
        <ul className="space-y-3 list-none pl-2">
          {principles.items.map((item, i) => (
            <li key={i} className="flex gap-3 text-navy-700 leading-relaxed">
              <span className="flex shrink-0 items-center text-navy-600">•</span>
              <div>
                <span className="font-semibold navy-text">{item.heading}</span>{" "}
                {item.body}
              </div>
            </li>
          ))}
        </ul>
      </section>

      {/* Stack */}
      <section className="space-y-4">
        <h2 className="text-xl font-bold navy-text">{stack.title}</h2>
        <p className="text-navy-700 leading-relaxed">{stack.intro}</p>
        <ul className="grid md:grid-cols-2 gap-4 list-none pl-2">
          {stack.items.map((item, i) => (
            <li key={i} className="border border-navy-100 rounded-lg p-4 text-navy-700">
              <p className="font-semibold navy-text mb-1">{item.name}</p>
              <p className="text-sm">{item.note}</p>
            </li>
          ))}
        </ul>
        <p className="mt-3 text-xs text-navy-500">
          المرشحان (Docling و PptxGenJS) غير مدمجين بعد — awaiting benchmarks and license review.
          / Les candidats (Docling, PptxGenJS) ne sont pas intégrés — en attente de benchmarks et de revue de licence.
        </p>
      </section>

      {/* Status */}
      <section className="space-y-4">
        <h2 className="text-xl font-bold navy-text">{status.title}</h2>
        <p className="text-sm text-navy-600">{status.note}</p>
        <table className="w-full border-collapse border border-navy-200 text-sm text-navy-700">
          <caption className="sr-only">{status.table.area} — {status.table.state}</caption>
          <thead>
            <tr className="border-b border-navy-200 bg-navy-50">
              <th className="text-right p-3 font-medium">{status.table.area}</th>
              <th className="text-right p-3 font-medium">{status.table.state}</th>
            </tr>
          </thead>
          <tbody>
            {status.rows.map((row, i) => (
              <tr key={i} className={i % 2 === 0 ? "bg-navy-50" : ""}>
                <td className="border-b border-navy-100 p-3">{row.area}</td>
                <td className="border-b border-navy-100 p-3">{row.state}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      {/* Integrity */}
      <section className="space-y-4 border-t border-navy-200 pt-8">
        <h2 className="text-xl font-bold navy-text">{integrity.title}</h2>
        <ul className="space-y-3 list-disc pl-6 text-navy-700 leading-relaxed">
          {integrity.items.map((item, i) => (
            <li key={i}>{item}</li>
          ))}
        </ul>
      </section>
    </div>
  );
}
