import type { Metadata } from "next";
import { LocalesProvider } from "@/components/locale-provider";
import { Header } from "@/components/header";
import { Footer } from "@/components/footer";
import "@/app/globals.css";

export const metadata: Metadata = {
  title: {
    template: "%s — MAFHUM",
    default: "MAFHUM — منصة تعليمية وبحثية للطلبة",
  },
  description:
    "منصة عربية أولًا تساعد الطلبة على فهم الدروس والعمل على بحوث أكاديمية موثقة وإعداد Fiche TD والعروض التقديمية القابلة للتعديل. الجزائر أولًا، ثم العالم العربي. واجهة RTL حقيقية مع دعم فرنسي.",
  openGraph: {
    siteName: "MAFHUM",
    locale: "ar_DZ",
  },
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="ar" dir="rtl" className="h-full">
      <body className="min-h-full bg-white text-navy-900 antialiased">
        <a href="#skip-content" className="skip-link">
          الانتقال إلى المحتوى principal / Aller au contenu principal
        </a>
        <LocalesProvider>
          <Header />
          <main id="skip-content" className="container mx-auto px-4 py-6">
            {children}
          </main>
          <Footer />
        </LocalesProvider>
      </body>
    </html>
  );
}
