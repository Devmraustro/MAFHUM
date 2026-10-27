"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";

import { DEFAULT_LOCALE, directionOf, makeTranslator } from "@/lib/i18n";
import type { Locale } from "@/lib/i18n";

const STORAGE_KEY = "mafhum.locale";

interface LocaleContextValue {
  locale: Locale;
  setLocale: (locale: Locale) => void;
  t: (key: string) => string;
  dir: "rtl" | "ltr";
}

const LocaleContext = createContext<LocaleContextValue | null>(null);

function isLocale(value: unknown): value is Locale {
  return value === "ar" || value === "fr";
}

/**
 * Locale provider.
 *
 * The document element's `lang` and `dir` are updated here so the *whole* page
 * (including browser chrome text direction and form controls) follows the chosen
 * locale. Persisted in localStorage so a returning student keeps their language.
 */
/**
 * Read the persisted choice during the initial render rather than in an effect,
 * so there is no second render and no flash of the wrong text direction.
 * On the server there is no storage, and Arabic is the default anyway — which
 * is also what `layout.tsx` renders, so the two never disagree.
 */
function initialLocale(): Locale {
  if (typeof window === "undefined") return DEFAULT_LOCALE;
  try {
    const stored = window.localStorage.getItem(STORAGE_KEY);
    return isLocale(stored) ? stored : DEFAULT_LOCALE;
  } catch {
    // Storage may be unavailable (private mode); the default is fine.
    return DEFAULT_LOCALE;
  }
}

export function LocaleProvider({ children }: { children: ReactNode }) {
  const [locale, setLocaleState] = useState<Locale>(initialLocale);

  useEffect(() => {
    const root = document.documentElement;
    root.lang = locale;
    root.dir = directionOf(locale);
  }, [locale]);

  const setLocale = useCallback((next: Locale) => {
    setLocaleState(next);
    try {
      window.localStorage.setItem(STORAGE_KEY, next);
    } catch {
      // Non-fatal: the choice still applies for this session.
    }
  }, []);

  const value = useMemo<LocaleContextValue>(
    () => ({
      locale,
      setLocale,
      t: makeTranslator(locale),
      dir: directionOf(locale),
    }),
    [locale, setLocale],
  );

  return <LocaleContext.Provider value={value}>{children}</LocaleContext.Provider>;
}

export function useLocale(): LocaleContextValue {
  const context = useContext(LocaleContext);
  if (!context) throw new Error("useLocale must be used inside LocaleProvider");
  return context;
}
