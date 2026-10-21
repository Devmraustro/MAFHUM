"use client";

import { useLocales } from "@/components/locale-provider";

export function Footer() {
  const { dictionary } = useLocales();
  const { footer, site } = dictionary;

  return (
    <footer className="mt-12 border-t border-navy-100 bg-navy-50">
      <div className="container mx-auto px-4 py-10">
        <div className="flex flex-col md:flex-row md:items-center justify-between gap-4 text-sm text-navy-700">
          <div className="font-medium navy-text">{site.name}</div>
          <p className="max-w-md leading-relaxed">{footer.builtWith}</p>
          <div className="flex flex-col sm:flex-row gap-3 text-xs text-navy-600">
            <span>{footer.license}</span>
            <span aria-hidden="true">·</span>
            <span>{footer.budget}</span>
            <span aria-hidden="true">·</span>
            <span>{footer.noPaidApis}</span>
          </div>
        </div>
        <div className="mt-6 border-t border-navy-200 pt-6 text-xs text-navy-500">
          {footer.draftNotice}
        </div>
      </div>
    </footer>
  );
}
