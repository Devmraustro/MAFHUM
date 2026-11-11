"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";

import { ApiError, account, auth } from "@/lib/api";
import { useLocale } from "@/lib/locale-context";

/**
 * Email verification landing page.
 *
 * The token is redeemed as soon as the page mounts, because the link is the
 * action — there is nothing for the user to confirm twice. The three outcomes
 * (verified, failed, no token) are reported distinctly: "invalid or expired"
 * tells the user to request a new link, which is the only useful next step.
 */
function VerifyInner() {
  const { t } = useLocale();
  const params = useSearchParams();
  const token = params.get("token");
  const [state, setState] = useState<"working" | "done" | "failed" | "none">(
    token ? "working" : "none",
  );
  const [message, setMessage] = useState<string | null>(null);

  useEffect(() => {
    if (!token) return;
    let cancelled = false;
    void (async () => {
      try {
        await auth.ensureAnonymousCsrf();
        const result = await account.confirmEmailVerification(token);
        if (!cancelled) {
          setMessage(result.message);
          setState("done");
        }
      } catch (error) {
        if (cancelled) return;
        setMessage(error instanceof ApiError ? error.message : null);
        setState("failed");
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [token]);

  return (
    <div className="card auth-card">
      <h1>{t("verify.title")}</h1>
      {state === "working" ? (
        <p role="status" aria-live="polite">
          <span className="spinner" aria-hidden="true" /> {t("common.loading")}
        </p>
      ) : null}
      {state === "done" ? (
        <p className="notice notice-success" role="status">
          {message ?? t("verify.done")}
        </p>
      ) : null}
      {state === "failed" ? (
        <p className="notice notice-danger" role="alert">
          {t("verify.failed")}
          {message ? ` — ${message}` : ""}
        </p>
      ) : null}
      {state === "none" ? <p className="hint">{t("verify.subtitle")}</p> : null}
      <p className="hint" style={{ marginBlockStart: "1rem" }}>
        <Link href="/account">{t("account.title")}</Link>
      </p>
    </div>
  );
}

export default function VerifyEmailPage() {
  return (
    <Suspense fallback={<p role="status">…</p>}>
      <VerifyInner />
    </Suspense>
  );
}
