"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";
import type { FormEvent } from "react";

import { ErrorAlert, FieldError } from "@/components/alerts";
import { ApiError, account, auth } from "@/lib/api";
import { useLocale } from "@/lib/locale-context";

/**
 * Password reset.
 *
 * Two states on one route, chosen by the presence of `?token=`:
 *
 * - **request** — ask for the address, then say exactly what happened. When this
 *   deployment has no mail transport the server answers 503 and the page says
 *   so instead of claiming a message was sent.
 * - **confirm** — set a new password. The token comes from the URL, so the page
 *   never asks the user to copy it by hand.
 *
 * The page is wrapped in `<Suspense>` because `useSearchParams` suspends during
 * static prerendering; without it the build fails rather than degrading.
 */
function ResetInner() {
  const { t } = useLocale();
  const params = useSearchParams();
  const token = params.get("token");

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [done, setDone] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [formError, setFormError] = useState<string | null>(null);
  const [fieldErrors, setFieldErrors] = useState<Record<string, string[]>>({});

  // A confirm submission needs a CSRF cookie before the POST; a request does too.
  useEffect(() => {
    void auth.ensureAnonymousCsrf();
  }, []);

  async function handleRequest(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSubmitting(true);
    setFormError(null);
    setMessage(null);
    try {
      const result = await account.requestPasswordReset(email);
      setMessage(result.message);
    } catch (error) {
      if (error instanceof ApiError) {
        // 503 has its own, more useful message: nothing is configured, so the
        // user should not sit waiting for an email that will never arrive.
        setFormError(error.code === "email_delivery_unavailable" ? t("auth.resetUnavailable") : error.message);
        setFieldErrors(error.fieldErrors);
      } else {
        setFormError(t("error.generic"));
      }
    } finally {
      setSubmitting(false);
    }
  }

  async function handleConfirm(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setFormError(null);
    setFieldErrors({});
    if (password !== confirm) {
      setFormError(t("auth.passwordsDiffer"));
      return;
    }
    if (password.length < 8) {
      setFormError(t("auth.passwordTooShort"));
      return;
    }
    setSubmitting(true);
    try {
      await account.confirmPasswordReset(token ?? "", password);
      setDone(true);
    } catch (error) {
      if (error instanceof ApiError) {
        // A used or expired link is not a form error: asking the user to fix
        // their password input would be misleading.
        setFormError(error.code === "invalid_token" ? t("auth.resetInvalidToken") : error.message);
      } else {
        setFormError(t("error.generic"));
      }
    } finally {
      setSubmitting(false);
    }
  }

  if (done) {
    return (
      <div className="card auth-card">
        <h1>{t("auth.resetNewTitle")}</h1>
        <p className="notice notice-success" role="status">
          {t("auth.resetDone")}
        </p>
        <Link className="btn btn-primary" href="/login">
          {t("auth.backToSignIn")}
        </Link>
      </div>
    );
  }

  if (token) {
    return (
      <div className="card auth-card">
        <h1>{t("auth.resetNewTitle")}</h1>
        <p className="hint">{t("auth.resetNewSubtitle")}</p>
        {formError ? <ErrorAlert message={formError} onClose={() => setFormError(null)} /> : null}
        <form onSubmit={handleConfirm} noValidate>
          <div className="field">
            <label htmlFor="new-password">{t("auth.newPassword")}</label>
            <input
              id="new-password"
              type="password"
              dir="ltr"
              autoComplete="new-password"
              required
              minLength={8}
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              aria-invalid={Boolean(fieldErrors.password)}
            />
            <FieldError messages={fieldErrors.password} />
          </div>
          <div className="field">
            <label htmlFor="confirm-password">{t("auth.confirmPassword")}</label>
            <input
              id="confirm-password"
              type="password"
              dir="ltr"
              autoComplete="new-password"
              required
              minLength={8}
              value={confirm}
              onChange={(event) => setConfirm(event.target.value)}
            />
          </div>
          <button className="btn btn-primary" type="submit" disabled={submitting}>
            {submitting ? (
              <>
                <span className="spinner" aria-hidden="true" /> {t("common.loading")}
              </>
            ) : (
              t("auth.resetSubmit")
            )}
          </button>
        </form>
        <p className="hint" style={{ marginBlockStart: "1rem" }}>
          <Link href="/reset-password">{t("auth.resetTitle")}</Link>
        </p>
      </div>
    );
  }

  return (
    <div className="card auth-card">
      <h1>{t("auth.resetTitle")}</h1>
      <p className="hint">{t("auth.resetSubtitle")}</p>
      {formError ? <ErrorAlert message={formError} onClose={() => setFormError(null)} /> : null}
      {message ? (
        <p className="notice notice-success" role="status">
          {message}
        </p>
      ) : null}
      <form onSubmit={handleRequest} noValidate>
        <div className="field">
          <label htmlFor="reset-email">{t("auth.email")}</label>
          <input
            id="reset-email"
            type="email"
            dir="ltr"
            autoComplete="email"
            required
            value={email}
            onChange={(event) => setEmail(event.target.value)}
            aria-invalid={Boolean(fieldErrors.email)}
          />
          <FieldError messages={fieldErrors.email} />
        </div>
        <button className="btn btn-primary" type="submit" disabled={submitting}>
          {submitting ? (
            <>
              <span className="spinner" aria-hidden="true" /> {t("common.loading")}
            </>
          ) : (
            t("auth.resetSend")
          )}
        </button>
      </form>
      <p className="hint" style={{ marginBlockStart: "1rem" }}>
        <Link href="/login">{t("auth.backToSignIn")}</Link>
      </p>
    </div>
  );
}

export default function ResetPasswordPage() {
  return (
    <Suspense fallback={<p role="status">…</p>}>
      <ResetInner />
    </Suspense>
  );
}
