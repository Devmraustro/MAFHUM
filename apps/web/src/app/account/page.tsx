"use client";

import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import type { FormEvent } from "react";

import { ErrorAlert, FieldError } from "@/components/alerts";
import { ConfirmDialog } from "@/components/confirm-dialog";
import { ApiError, account, auth } from "@/lib/api";
import type { AccountState, SessionItem } from "@/lib/api";
import { useLocale } from "@/lib/locale-context";

/**
 * Account settings: address state, password change, and active sessions.
 *
 * Two honesty rules are visible here:
 * - When the deployment cannot send mail, the verification button is replaced by
 *   an explanation. Offering a button that always fails would be worse than
 *   saying nothing.
 * - Session revocation shows the current device and asks before ending another
 *   session, because a mis-click there signs the user out of a device they may
 *   not be holding.
 */
export default function AccountPage() {
  const { t, locale } = useLocale();
  const router = useRouter();

  const [state, setState] = useState<AccountState | null>(null);
  const [sessions, setSessions] = useState<SessionItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [pageError, setPageError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [changingPassword, setChangingPassword] = useState(false);
  const [passwordErrors, setPasswordErrors] = useState<Record<string, string[]>>({});

  const [verifying, setVerifying] = useState(false);
  const [pendingRevoke, setPendingRevoke] = useState<SessionItem | null>(null);

  /** Fetch both panels; returns data so no state is written from an effect. */
  const fetchAccount = useCallback(async () => {
    await auth.ensureCsrf();
    const user = await auth.me();
    if (!user) return null;
    const [accountState, sessionList] = await Promise.all([account.state(), account.sessions()]);
    return { accountState, sessions: sessionList.items };
  }, []);

  // Initial load. Every state write happens inside the async callback, never
  // synchronously in the effect body.
  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const result = await fetchAccount();
        if (cancelled) return;
        if (!result) {
          router.replace("/login");
          return;
        }
        setState(result.accountState);
        setSessions(result.sessions);
      } catch (error) {
        if (cancelled) return;
        if (error instanceof ApiError && error.status === 401) {
          router.replace("/login");
          return;
        }
        setPageError(t("account.error"));
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [fetchAccount, router, t]);

  async function handleChangePassword(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setChangingPassword(true);
    setPageError(null);
    setNotice(null);
    setPasswordErrors({});
    try {
      const result = await account.changePassword(currentPassword, newPassword);
      setNotice(result.message || t("account.changePasswordDone"));
      setCurrentPassword("");
      setNewPassword("");
      // A password change ends every other session, so the list is stale now.
      const sessionList = await account.sessions();
      setSessions(sessionList.items);
    } catch (error) {
      if (error instanceof ApiError) {
        setPasswordErrors(error.fieldErrors);
        if (Object.keys(error.fieldErrors).length === 0) setPageError(error.message);
      } else {
        setPageError(t("account.error"));
      }
    } finally {
      setChangingPassword(false);
    }
  }

  async function handleRequestVerification() {
    setVerifying(true);
    setPageError(null);
    setNotice(null);
    try {
      const result = await account.requestEmailVerification();
      setNotice(result.message || t("account.verifySent"));
    } catch (error) {
      if (error instanceof ApiError) {
        setPageError(
          error.code === "email_delivery_unavailable"
            ? t("account.verifyUnavailable")
            : error.message,
        );
      } else {
        setPageError(t("account.error"));
      }
    } finally {
      setVerifying(false);
    }
  }

  async function handleRevoke() {
    const target = pendingRevoke;
    setPendingRevoke(null);
    if (!target) return;
    setPageError(null);
    try {
      await account.revokeSession(target.id);
      if (target.current) {
        router.replace("/login");
        return;
      }
      setSessions((current) => current.filter((item) => item.id !== target.id));
      setNotice(t("account.sessionRevoke"));
    } catch (error) {
      setPageError(error instanceof ApiError ? error.message : t("account.error"));
    }
  }

  function formatDate(value: string): string {
    try {
      return new Intl.DateTimeFormat(locale === "ar" ? "ar-DZ" : "fr-FR", {
        dateStyle: "medium",
        timeStyle: "short",
      }).format(new Date(value));
    } catch {
      return value;
    }
  }

  if (loading) {
    return (
      <p role="status" aria-live="polite">
        <span className="spinner" aria-hidden="true" /> {t("common.loading")}
      </p>
    );
  }

  return (
    <div className="page-stack">
      <header className="page-head">
        <h1>{t("account.title")}</h1>
        <p className="hint">{t("account.subtitle")}</p>
      </header>

      {pageError ? <ErrorAlert message={pageError} onClose={() => setPageError(null)} /> : null}
      {notice ? (
        <p className="notice notice-success" role="status">
          {notice}
        </p>
      ) : null}

      <section className="card" aria-labelledby="account-email">
        <h2 id="account-email">{t("account.emailLabel")}</h2>
        <p dir="ltr" className="account-email">
          {state?.email}
        </p>
        <p>
          {state?.email_verified ? (
            <span className="badge badge-success">{t("account.verified")}</span>
          ) : (
            <span className="badge badge-warning">{t("account.unverified")}</span>
          )}
        </p>
        {state && !state.email_verified ? (
          state.email_delivery_configured ? (
            <>
              <p className="hint">{t("account.verifyWhy")}</p>
              <button
                className="btn btn-secondary"
                type="button"
                onClick={handleRequestVerification}
                disabled={verifying}
              >
                {verifying ? (
                  <>
                    <span className="spinner" aria-hidden="true" /> {t("common.loading")}
                  </>
                ) : (
                  t("account.verifyNow")
                )}
              </button>
            </>
          ) : (
            <p className="hint">{t("account.verifyUnavailable")}</p>
          )
        ) : null}
      </section>

      <section className="card" aria-labelledby="account-password">
        <h2 id="account-password">{t("account.changePassword")}</h2>
        <form onSubmit={handleChangePassword} noValidate className="form-grid">
          <div className="field">
            <label htmlFor="current-password">{t("account.currentPassword")}</label>
            <input
              id="current-password"
              type="password"
              dir="ltr"
              autoComplete="current-password"
              required
              value={currentPassword}
              onChange={(event) => setCurrentPassword(event.target.value)}
              aria-invalid={Boolean(passwordErrors.current_password)}
            />
            <FieldError messages={passwordErrors.current_password} />
          </div>
          <div className="field">
            <label htmlFor="account-new-password">{t("auth.newPassword")}</label>
            <input
              id="account-new-password"
              type="password"
              dir="ltr"
              autoComplete="new-password"
              required
              minLength={8}
              value={newPassword}
              onChange={(event) => setNewPassword(event.target.value)}
              aria-invalid={Boolean(passwordErrors.new_password)}
            />
            <FieldError messages={passwordErrors.new_password} />
          </div>
          <div>
            <button className="btn btn-primary" type="submit" disabled={changingPassword}>
              {changingPassword ? (
                <>
                  <span className="spinner" aria-hidden="true" /> {t("common.loading")}
                </>
              ) : (
                t("account.changePasswordSubmit")
              )}
            </button>
          </div>
        </form>
      </section>

      <section className="card" aria-labelledby="account-sessions">
        <h2 id="account-sessions">{t("account.sessions")}</h2>
        <p className="hint">{t("account.sessionsLead")}</p>
        {sessions.length === 0 ? (
          <p className="hint">{t("account.sessionsEmpty")}</p>
        ) : (
          <ul className="session-list">
            {sessions.map((session) => (
              <li key={session.id}>
                <div>
                  <p className="session-agent" dir="ltr">
                    {session.user_agent ?? "—"}
                  </p>
                  <p className="hint">
                    {t("account.sessionCreated")}: {formatDate(session.created_at)} ·{" "}
                    {t("account.sessionExpires")}: {formatDate(session.expires_at)}
                  </p>
                  {session.current ? (
                    <span className="badge badge-info">{t("account.sessionCurrent")}</span>
                  ) : null}
                </div>
                <button
                  className="btn btn-sm btn-danger"
                  type="button"
                  onClick={() => setPendingRevoke(session)}
                >
                  {t("account.sessionRevoke")}
                </button>
              </li>
            ))}
          </ul>
        )}
      </section>

      <ConfirmDialog
        title={t("account.sessionRevoke")}
        message={t("account.sessionRevokeConfirm")}
        confirmLabel={t("account.sessionRevoke")}
        cancelLabel={t("common.cancel")}
        onConfirm={handleRevoke}
        onCancel={() => setPendingRevoke(null)}
      />
    </div>
  );
}
