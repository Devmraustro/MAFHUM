"use client";

import Link from "next/link";
import { useLocales } from "@/components/locale-provider";
import { LocaleSwitcher } from "@/components/locale-switcher";

export function Header() {
  const { dictionary, locale } = useLocales();
  const { nav, site } = dictionary;

  return (
    <header className="sticky top-0 z-40 border-b border-navy-100 bg-white/95 backdrop-blur supports-[backdrop-blur]:bg-white/80">
      <div className="container mx-auto px-4 h-14 flex items-center justify-between">
        <Link
          href="/"
          className="flex items-center gap-2 navy-text font-bold text-lg tracking-tight"
          aria-label={`${site.name} — الرئيسية`}
        >
          <span className="bg-navy text-white rounded px-2 py-0.5 text-xs font-mono">MAFHUM</span>
          <span className="font-semibold">منصة تعليمية وبحثية</span>
        </Link>

        <nav className="hidden md:flex items-center gap-6 text-sm font-medium">
          {[
            { href: "/", key: nav.home },
            { href: "/about", key: nav.about },
            { href: "/privacy", key: nav.privacy },
            { href: "/status", key: nav.status },
            { href: "https://github.com/Devmraustro/MAFHUM", key: nav.docs, external: true },
          ].map((item) => (
            <Link
              key={item.key}
              href={item.href}
              className={`rounded-md px-3 py-2 transition-colors ${
                item.href === "/" ? "bg-navy-100 navy-text" : "text-navy-700 hover:bg-navy-50 hover:navy-text"
              } ${item.external ? "" : ""}`}
              target={item.external ? "_blank" : undefined}
              rel={item.external ? "noopener noreferrer" : undefined}
            >
              {item.key}
            </Link>
          ))}
        </nav>

        <div className="flex items-center gap-3">
          <LocaleSwitcher current={locale} />
          <button
            className="rounded-md border border-navy/30 bg-navy px-3.5 py-2 text-sm font-semibold text-white shadow-sm hover:bg-navy-800 transition-colors"
            aria-controls="skip-content"
          >
            للطلبة
          </button>
        </div>
      </div>
    </header>
  );
}
