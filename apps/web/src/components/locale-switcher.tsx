"use client";

import { useLocales } from "@/components/locale-provider";

export function LocaleSwitcher({ current }: { current: "ar" | "fr" }) {
  const { setLocale, dictionary } = useLocales();
  const { localeSwitcher } = dictionary;

  return (
    <div className="relative inline-block" role="group" aria-label={localeSwitcher.label}>
      <button
        className="rounded-md border border-navy/30 bg-white px-3 py-2 text-sm font-medium text-navy-700 shadow-sm hover:bg-navy-50 focus-visible:border-navy-600 transition-colors"
        aria-haspopup="listbox"
        aria-expanded="false"
        onClick={() => {
          setLocale(current === "ar" ? "fr" : "ar");
        }}
      >
        {current === "ar" ? localeSwitcher.arabic : localeSwitcher.french}
      </button>
    </div>
  );
}
