"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import type { FormEvent } from "react";

import { ErrorAlert, FieldError } from "@/components/alerts";
import { ApiError, auth } from "@/lib/api";
import { useLocale } from "@/lib/locale-context";

type Mode = "login" | "register";

/**
 * Sign-in / registration page.
 *
 * Both forms post to the real API. Errors come back as the server's own Arabic
 * messages, mapped onto the fields the server named.
 */
export default function AuthPage() {
  const { t } = useLocale();
  const router = useRouter();

  const [mode, setMode] = useState<Mode>("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [fieldErrors, setFieldErrors] = useState<Record<string, string[]>>({});
  const [checking, setChecking] = useState(true);

  // If a session already exists, go straight to the workspace.
  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        await auth.ensureCsrf();
        const user = await auth.me();
        if (user && !cancelled) router.replace("/documents");
      } catch {
        // Unauthenticated: stay on this page.
      } finally {
        if (!cancelled) setChecking(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [router]);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSubmitting(true);
    setFormError(null);
    setFieldErrors({});
    try {
      if (mode === "login") {
        await auth.login(email, password);
      } else {
        await auth.register(email, password, displayName);
      }
      // A fresh session needs a fresh CSRF cookie before any mutation.
      await auth.ensureCsrf();
      router.push("/documents");
    } catch (error) {
      if (error instanceof ApiError) {
        setFormError(error.message);
        setFieldErrors(error.fieldErrors);
      } else {
        setFormError(t("error.generic"));
      }
    } finally {
      setSubmitting(false);
    }
  }

  if (checking) {
    return (
      <p role="status" aria-live="polite">
        <span className="spinner" aria-hidden="true" /> {t("common.loading")}
      </p>
    );
  }

  return (
    <div className="card" style={{ maxWidth: "32rem", marginInline: "auto" }}>
      <h1>{t("login.title")}</h1>
      <p className="hint">{t("login.subtitle")}</p>

      {formError ? <ErrorAlert message={formError} onClose={() => setFormError(null)} /> : null}

      <form onSubmit={handleSubmit} noValidate>
        <div className="field">
          <label htmlFor="email">{t("auth.email")}</label>
          <input
            id="email"
            name="email"
            type="email"
            dir="ltr"
            autoComplete="email"
            required
            value={email}
            onChange={(event) => setEmail(event.target.value)}
            aria-invalid={Boolean(fieldErrors.email)}
            aria-describedby={fieldErrors.email ? "field-error" : undefined}
          />
          <FieldError messages={fieldErrors.email} />
        </div>

        {mode === "register" ? (
          <div className="field">
            <label htmlFor="display_name">
              {t("auth.displayName")} <span className="hint">({t("common.optional")})</span>
            </label>
            <input
              id="display_name"
              name="display_name"
              type="text"
              autoComplete="nickname"
              maxLength={120}
              value={displayName}
              onChange={(event) => setDisplayName(event.target.value)}
            />
          </div>
        ) : null}

        <div className="field">
          <label htmlFor="password">{t("auth.password")}</label>
          <input
            id="password"
            name="password"
            type="password"
            dir="ltr"
            autoComplete={mode === "login" ? "current-password" : "new-password"}
            required
            minLength={mode === "register" ? 8 : undefined}
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            aria-invalid={Boolean(fieldErrors.password)}
          />
          <FieldError messages={fieldErrors.password} />
        </div>

        <button className="btn btn-primary" type="submit" disabled={submitting}>
          {submitting ? (
            <>
              <span className="spinner" aria-hidden="true" /> {t("common.loading")}
            </>
          ) : mode === "login" ? (
            t("auth.signin")
          ) : (
            t("auth.signup")
          )}
        </button>
      </form>

      <p className="hint" style={{ marginBlockStart: "1rem" }}>
        {mode === "login" ? t("auth.noAccount") : t("auth.haveAccount")}{" "}
        <button
          type="button"
          className="btn btn-sm btn-secondary"
          onClick={() => {
            setMode(mode === "login" ? "register" : "login");
            setFormError(null);
            setFieldErrors({});
          }}
        >
          {mode === "login" ? t("auth.signup") : t("auth.signin")}
        </button>
      </p>
    </div>
  );
}
