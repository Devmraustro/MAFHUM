"use client";
import Link from "next/link";
import { useLocales } from "@/components/locale-provider";

export default function About() {
  const { dictionary } = useLocales();
  const { about, integrity, footer } = dictionary;

  return (
    <div className="space-y-8">
      <section>
        <h1 className="text-2xl font-bold navy-text">{about.title}</h1>
        <p className="text-navy-700 leading-relaxed">{about.intro}</p>
        <p className="text-navy-700 leading-relaxed mt-4">{about.mission}</p>
      </section>

      <section className="space-y-4 border-t border-navy-200 pt-6">
        <h2 className="text-xl font-bold navy-text">{integrity.title}</h2>
        <ul className="space-y-3 list-disc pl-6 text-navy-700 leading-relaxed">
          {integrity.items.map((item, i) => (
            <li key={i}>{item}</li>
          ))}
        </ul>
      </section>

      <section className="space-y-3 text-sm text-navy-600 border-t border-navy-200 pt-6">
        <p>
          <strong>العلامة التجارية:</strong> MAFHUM فقط. لا “MAFHUM AI” ولا “DZ University AI”.
        </p>
        <p>
          <strong>الميزانية:</strong> 0 دج — مرحلة أولى مجانية. لا APIs إلزامية مدفوعة.
        </p>
        <p>
          <strong>مستودع المشروع:</strong>
          <Link
            href="https://github.com/Devmraustro/MAFHUM"
            target="_blank"
            rel="noopener noreferrer"
            className="navy-text underline underline-offset-2 hover:navy-800"
          >
            github.com/Devmraustro/MAFHUM
          </Link>
        </p>
        <p>{footer.draftNotice}</p>
      </section>
    </div>
  );
}
