import type { Metadata, Viewport } from "next";
import type { ReactNode } from "react";

import "./globals.css";
import { LocaleProvider } from "@/lib/locale-context";
import { SiteHeader } from "@/components/site-header";

export const metadata: Metadata = {
  title: "MAFHUM — منصة تعليمية وبحثية للطلبة",
  description:
    "MAFHUM: ارفع مستنداتك الدراسية (PDF و DOCX) واستخرج نصوصها بأمان. منصة عربية الاتجاه للطلبة في الجزائر والعالم العربي.",
  applicationName: "MAFHUM",
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  themeColor: "#0b2545",
};

/**
 * Root layout.
 *
 * `lang="ar"` and `dir="rtl"` are set on the server so the very first paint is
 * already right-to-left; the locale provider refines them if the student switches
 * to French. Arabic is the default because it is the primary audience's language.
 */
export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="ar" dir="rtl">
      <body>
        <LocaleProvider>
          <a className="skip-link" href="#main-content">
            تخطَّ إلى المحتوى
          </a>
          <SiteHeader />
          <main id="main-content" tabIndex={-1}>
            {children}
          </main>
        </LocaleProvider>
      </body>
    </html>
  );
}
