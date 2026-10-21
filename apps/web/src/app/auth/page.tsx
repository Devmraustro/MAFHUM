"use client";

import { FormEvent, useState } from "react";
import { useRouter } from "next/navigation";
import { useLocales } from "@/components/locale-provider";
import { api, ApiClientError } from "@/lib/api";
import type { Locale } from "@/lib/contract";

export default function AuthPage() {
  const router = useRouter();
  const { dictionary, setLocale } = useLocales();
  const t = dictionary.auth;

  const [mode, setMode] = useState<"login" | "register">("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [fullName, setFullName] = useState("");
  const [preferredLocale, setPreferredLocale] = useState<Locale>(dictionary.locale);
  const [submitting, setSubmitting] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [requestId, setRequestId] = useState<string | null>(null);

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    setSubmitting(true);
    setErrorMessage(null);
    setRequestId(null);

    try {
      const user =
        mode === "register"
          ? await api.register({
              email,
              password,
              full_name: fullName.trim() || null,
              preferred_locale: preferredLocale,
            })
          : await api.login({ email, password });

      setLocale(user.preferred_locale);
      router.push("/workspace");
    } catch (err) {
      if (err instanceof ApiClientError) {
        const fieldDetail =
          err.fieldErrors.length > 0
            ? `${err.message} (${err.fieldErrors.map((f) => `${f.field}: ${f.message}`).join(", ")})`
            : err.message;
        setErrorMessage(fieldDetail);
        setRequestId(err.requestId || null);
      } else {
        setErrorMessage("Request failed");
      }
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="max-w-lg mx-auto border border-navy-200 rounded-xl bg-white p-6 md:p-8 shadow-sm space-y-6">
      <div className="flex gap-2 border-b border-navy-100 pb-4" role="tablist">
        <button
          type="button"
          role="tab"
          aria-selected={mode === "login"}
          onClick={() => {
            setMode("login");
            setErrorMessage(null);
          }}
          className={`flex-1 rounded-md px-4 py-2 text-sm font-semibold transition-colors ${
            mode === "login"
              ? "bg-navy text-white"
              : "bg-navy-50 text-navy-700 hover:bg-navy-100"
          }`}
        >
          {t.loginTab}
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={mode === "register"}
          onClick={() => {
            setMode("register");
            setErrorMessage(null);
          }}
          className={`flex-1 rounded-md px-4 py-2 text-sm font-semibold transition-colors ${
            mode === "register"
              ? "bg-navy text-white"
              : "bg-navy-50 text-navy-700 hover:bg-navy-100"
          }`}
        >
          {t.registerTab}
        </button>
      </div>

      <h1 className="text-2xl font-bold navy-text">
        {mode === "login" ? t.loginTab : t.registerTab}
      </h1>

      {errorMessage && (
        <div
          role="alert"
          className="rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-900 space-y-1"
        >
          <p>{errorMessage}</p>
          {requestId && (
            <p className="text-xs font-mono" dir="ltr">
              {t.requestIdLabel}: {requestId}
            </p>
          )}
        </div>
      )}

      <form onSubmit={handleSubmit} className="space-y-4">
        <div>
          <label htmlFor="auth-email" className="block text-sm font-medium navy-text mb-1">
            {t.emailLabel}
          </label>
          <input
            id="auth-email"
            type="email"
            dir="ltr"
            required
            autoComplete="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            className="w-full rounded-md border border-navy-200 px-3 py-2 text-sm text-navy-900 focus:border-navy focus:outline-none"
          />
        </div>

        <div>
          <label htmlFor="auth-password" className="block text-sm font-medium navy-text mb-1">
            {t.passwordLabel}
          </label>
          <input
            id="auth-password"
            type="password"
            required
            minLength={mode === "register" ? 8 : 1}
            maxLength={128}
            autoComplete={mode === "register" ? "new-password" : "current-password"}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            className="w-full rounded-md border border-navy-200 px-3 py-2 text-sm text-navy-900 focus:border-navy focus:outline-none"
          />
          {mode === "register" && (
            <p className="mt-1 text-xs text-navy-600">{t.passwordHint}</p>
          )}
        </div>

        {mode === "register" && (
          <>
            <div>
              <label htmlFor="auth-fullname" className="block text-sm font-medium navy-text mb-1">
                {t.fullNameLabel}
              </label>
              <input
                id="auth-fullname"
                type="text"
                maxLength={200}
                value={fullName}
                onChange={(e) => setFullName(e.target.value)}
                className="w-full rounded-md border border-navy-200 px-3 py-2 text-sm text-navy-900 focus:border-navy focus:outline-none"
              />
            </div>

            <div>
              <label htmlFor="auth-locale" className="block text-sm font-medium navy-text mb-1">
                {t.preferredLocaleLabel}
              </label>
              <select
                id="auth-locale"
                value={preferredLocale}
                onChange={(e) => setPreferredLocale(e.target.value as Locale)}
                className="w-full rounded-md border border-navy-200 bg-white px-3 py-2 text-sm text-navy-900 focus:border-navy focus:outline-none"
              >
                <option value="ar">العربية (RTL)</option>
                <option value="fr">Français (LTR)</option>
              </select>
            </div>
          </>
        )}

        <button
          type="submit"
          disabled={submitting}
          className="w-full rounded-md bg-navy px-4 py-2.5 text-sm font-semibold text-white hover:bg-navy-800 disabled:opacity-60 transition-colors"
        >
          {submitting ? t.submitting : mode === "login" ? t.submitLogin : t.submitRegister}
        </button>
      </form>
    </div>
  );
}
