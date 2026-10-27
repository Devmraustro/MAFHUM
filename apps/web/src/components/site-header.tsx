"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { auth } from "@/lib/api";
import type { User } from "@/lib/api";
import { useLocale } from "@/lib/locale-context";

/**
 * Site header: brand, primary navigation, locale switch and sign-out.
 *
 * The navigation is rendered from a real session check. There is no placeholder
 * user: when no session exists the header shows only the sign-in link.
 */
export function SiteHeader() {
  const { locale, setLocale, t } = useLocale();
  const pathname = usePathname();
  const router = useRouter();
  const [user, setUser] = useState<User | null>(null);
  const [signingOut, setSigningOut] = useState(false);

  useEffect(() => {
    let cancelled = false;
    void auth
      .me()
      .then((current) => {
        if (!cancelled) setUser(current);
      })
      .catch(() => {
        if (!cancelled) setUser(null);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  async function handleSignOut() {
    setSigningOut(true);
    try {
      await auth.logout();
    } catch {
      // Even if the server call fails, drop the local view of the session.
    }
    setUser(null);
    router.replace("/");
  }

  const navItems = [
    { href: "/documents", label: t("nav.documents") },
    { href: "/research", label: t("nav.research") },
    { href: "/study", label: t("nav.study") },
    { href: "/presentations", label: t("nav.presentations") },
  ];

  return (
    <header className="header">
      <div className="header-inner">
        <Link className="brand" href="/">
          <span className="brand-name">{t("app.name")}</span>
          <span className="brand-tagline">{t("app.tagline")}</span>
        </Link>

        <nav className="nav" aria-label={t("nav.documents")}>
          {user
            ? navItems.map((item) => (
                <Link
                  key={item.href}
                  href={item.href}
                  aria-current={pathname?.startsWith(item.href) ? "page" : undefined}
                >
                  {item.label}
                </Link>
              ))
            : null}

          <span className="locale-switch" role="group" aria-label="اللغة / Langue">
            <button
              type="button"
              aria-pressed={locale === "ar"}
              onClick={() => setLocale("ar")}
            >
              العربية
            </button>
            <button
              type="button"
              aria-pressed={locale === "fr"}
              onClick={() => setLocale("fr")}
            >
              Français
            </button>
          </span>

          {user ? (
            <button type="button" onClick={handleSignOut} disabled={signingOut}>
              {signingOut ? t("common.loading") : t("nav.signout")}
            </button>
          ) : (
            <Link href="/" aria-current={pathname === "/" ? "page" : undefined}>
              {t("auth.signin")}
            </Link>
          )}
        </nav>
      </div>
    </header>
  );
}
