"use client";

import { createContext, useContext, useEffect, useCallback, useState } from "react";
import type { Dictionary, Locale } from "@/lib/dictionary";
import { getDictionary, validateDictionary } from "@/lib/dictionary";

const LOCALES_KEY = "mafhum-locale";
const DEFAULT_LOCALE: Locale = "ar";

type LocalesContextValue = {
  locale: Locale;
  dictionary: Dictionary;
  setLocale: (locale: Locale) => void;
};

const LocalesContext = createContext<LocalesContextValue | undefined>(undefined);

function readStoredLocale(): Locale {
  if (typeof localStorage === "undefined") return DEFAULT_LOCALE;
  try {
    const raw = localStorage.getItem(LOCALES_KEY);
    if (raw === "ar" || raw === "fr") return raw;
  } catch {
    // LocalStorage may be unavailable in private browsing.
  }
  return DEFAULT_LOCALE;
}

export function LocalesProvider({ children }: { children: React.ReactNode }) {
  const [locale, setLocaleState] = useState<Locale>(readStoredLocale);

  const setLocale = useCallback((next: Locale) => {
    setLocaleState(next);
    try {
      localStorage.setItem(LOCALES_KEY, next);
    } catch {
      // LocalStorage may be unavailable in private browsing; fall back to memory only.
    }
  }, []);

  // Keep <html> lang/dir in sync with the chosen locale (external DOM, no setState).
  useEffect(() => {
    const dict = getDictionary(locale);
    document.documentElement.lang = dict.locale;
    document.documentElement.dir = dict.dir;
  }, [locale]);

  // Sanity gate: fail closed if a dictionary is malformed.
  useEffect(() => {
    const problems = validateDictionary(locale);
    if (problems.length > 0) {
      console.error(`[MAFHUM] Dictionary validation failed for ${locale}:`, problems);
    }
  }, [locale]);

  const dictionary = getDictionary(locale);

  return (
    <LocalesContext.Provider value={{ locale, dictionary, setLocale }}>
      {children}
    </LocalesContext.Provider>
  );
}

export function useLocales(): LocalesContextValue {
  const ctx = useContext(LocalesContext);
  if (!ctx) {
    throw new Error("useLocales must be used within a LocalesProvider");
  }
  return ctx;
}
