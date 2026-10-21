"use client";

import { FormEvent, useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useLocales } from "@/components/locale-provider";
import { api, ApiClientError } from "@/lib/api";
import type { Locale, SessionOut, UserOut } from "@/lib/contract";

export default function AccountPage() {
  const router = useRouter();
  const { dictionary, setLocale } = useLocales();
  const t = dictionary.accountPage;
  const authT = dictionary.auth;
  const wsT = dictionary.workspace;

  const [user, setUser] = useState<UserOut | null>(null);
  const [sessions, setSessions] = useState<SessionOut[]>([]);
  const [fullName, setFullName] = useState("");
  const [preferredLocale, setPreferredLocale] = useState<Locale>("ar");
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [loading, setLoading] = useState(true);
  const [unauthenticated, setUnauthenticated] = useState(false);
  const [statusMessage, setStatusMessage] = useState<string | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  const refreshAccount = useCallback(async () => {
    const [me, sessionList] = await Promise.all([api.getMe(), api.listSessions()]);
    setUser(me);
    setSessions(sessionList.items);
    setFullName(me.full_name ?? "");
    setPreferredLocale(me.preferred_locale);
    setUnauthenticated(false);
  }, []);

  useEffect(() => {
    let cancelled = false;
    Promise.all([api.getMe(), api.listSessions()])
      .then(([me, sessionList]) => {
        if (cancelled) return;
        setUser(me);
        setSessions(sessionList.items);
        setFullName(me.full_name ?? "");
        setPreferredLocale(me.preferred_locale);
        setUnauthenticated(false);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        if (err instanceof ApiClientError && err.status === 401) {
          setUnauthenticated(true);
        } else if (err instanceof Error) {
          setErrorMessage(err.message);
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const handleSaveProfile = async (e: FormEvent) => {
    e.preventDefault();
    setStatusMessage(null);
    setErrorMessage(null);
    try {
      const updated = await api.updateProfile({
        full_name: fullName.trim() || null,
        preferred_locale: preferredLocale,
      });
      setUser(updated);
      setLocale(updated.preferred_locale);
      setStatusMessage(t.savedMessage);
    } catch (err) {
      setErrorMessage(err instanceof Error ? err.message : "Update failed");
    }
  };

  const handleRevokeSession = async (sessionId: string) => {
    setErrorMessage(null);
    try {
      await api.revokeSession(sessionId);
      await refreshAccount();
    } catch (err) {
      setErrorMessage(err instanceof Error ? err.message : "Failed to revoke session");
    }
  };

  const handleLogout = async () => {
    await api.logout();
    router.push("/auth");
  };

  const handleLogoutAll = async () => {
    await api.logoutAll();
    router.push("/auth");
  };

  const handleExport = async () => {
    setErrorMessage(null);
    try {
      const payload = await api.exportAccount();
      const blob = new Blob([JSON.stringify(payload, null, 2)], {
        type: "application/json",
      });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `mafhum-export-${payload.user.id}.json`;
      a.click();
      URL.revokeObjectURL(url);
    } catch (err) {
      setErrorMessage(err instanceof Error ? err.message : "Export failed");
    }
  };

  const handleDeleteAccount = async () => {
    if (!confirmDelete) return;
    setErrorMessage(null);
    try {
      await api.deleteAccount();
      router.push("/");
    } catch (err) {
      setErrorMessage(err instanceof Error ? err.message : "Account deletion failed");
    }
  };

  if (unauthenticated && !loading) {
    return (
      <div className="max-w-lg mx-auto border border-navy-200 rounded-xl bg-navy-50 p-8 space-y-4">
        <h1 className="text-2xl font-bold navy-text">{wsT.signInRequiredTitle}</h1>
        <p className="text-navy-700">{wsT.signInRequiredBody}</p>
        <Link
          href="/auth"
          className="inline-block rounded-md bg-navy px-4 py-2.5 text-sm font-semibold text-white hover:bg-navy-800 transition-colors"
        >
          {wsT.goToAuth}
        </Link>
      </div>
    );
  }

  return (
    <div className="max-w-2xl mx-auto space-y-8">
      <div className="flex items-center justify-between gap-4 border-b border-navy-100 pb-4">
        <h1 className="text-2xl font-bold navy-text">{t.title}</h1>
        <button
          type="button"
          onClick={() => void handleLogout()}
          className="rounded-md border border-navy-200 px-3.5 py-2 text-xs font-semibold text-navy-800 hover:bg-navy-50"
        >
          {t.logoutButton}
        </button>
      </div>

      {statusMessage && (
        <div role="status" className="rounded-md border border-green-200 bg-green-50 p-3 text-sm text-green-900">
          {statusMessage}
        </div>
      )}

      {errorMessage && (
        <div role="alert" className="rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-900">
          {errorMessage}
        </div>
      )}

      {/* Profile section */}
      {user && (
        <section className="border border-navy-200 rounded-xl bg-white p-6 space-y-4">
          <h2 className="text-lg font-bold navy-text">{t.profileTitle}</h2>
          <p className="text-xs font-mono text-navy-600" dir="ltr">
            {user.email}
          </p>
          <form onSubmit={handleSaveProfile} className="space-y-4">
            <div>
              <label htmlFor="profile-fullname" className="block text-sm font-medium navy-text mb-1">
                {authT.fullNameLabel}
              </label>
              <input
                id="profile-fullname"
                type="text"
                maxLength={200}
                value={fullName}
                onChange={(e) => setFullName(e.target.value)}
                className="w-full rounded-md border border-navy-200 px-3 py-2 text-sm text-navy-900"
              />
            </div>
            <div>
              <label htmlFor="profile-locale" className="block text-sm font-medium navy-text mb-1">
                {authT.preferredLocaleLabel}
              </label>
              <select
                id="profile-locale"
                value={preferredLocale}
                onChange={(e) => setPreferredLocale(e.target.value as Locale)}
                className="w-full rounded-md border border-navy-200 bg-white px-3 py-2 text-sm text-navy-900"
              >
                <option value="ar">العربية (RTL)</option>
                <option value="fr">Français (LTR)</option>
              </select>
            </div>
            <button
              type="submit"
              className="rounded-md bg-navy px-4 py-2 text-sm font-semibold text-white hover:bg-navy-800"
            >
              {t.saveProfile}
            </button>
          </form>
        </section>
      )}

      {/* Active sessions section */}
      <section className="border border-navy-200 rounded-xl bg-white p-6 space-y-4">
        <div className="flex items-center justify-between gap-4">
          <h2 className="text-lg font-bold navy-text">{t.sessionsTitle}</h2>
          <button
            type="button"
            onClick={() => void handleLogoutAll()}
            className="rounded-md border border-navy-200 px-3 py-1.5 text-xs font-medium text-navy-800 hover:bg-navy-50"
          >
            {t.logoutAllButton}
          </button>
        </div>
        <ul className="space-y-2 list-none">
          {sessions.map((s) => (
            <li
              key={s.id}
              className="flex items-center justify-between gap-4 rounded-lg border border-navy-100 p-3 text-xs"
            >
              <div className="space-y-1">
                <span className="font-mono text-navy-800" dir="ltr">
                  {s.id.slice(0, 8)}
                </span>
                {s.is_current && (
                  <span className="ms-2 rounded bg-navy-100 px-2 py-0.5 font-semibold navy-text">
                    {t.currentSessionBadge}
                  </span>
                )}
              </div>
              {!s.is_current && (
                <button
                  type="button"
                  onClick={() => void handleRevokeSession(s.id)}
                  className="rounded border border-red-200 px-2.5 py-1 text-red-700 hover:bg-red-50"
                >
                  {t.revokeSessionButton}
                </button>
              )}
            </li>
          ))}
        </ul>
      </section>

      {/* Data export & Account deletion */}
      <section className="border border-navy-200 rounded-xl bg-white p-6 space-y-6">
        <div className="space-y-3">
          <h2 className="text-lg font-bold navy-text">{t.dataLifecycleTitle}</h2>
          <button
            type="button"
            onClick={() => void handleExport()}
            className="rounded-md border border-navy bg-navy-50 px-4 py-2 text-sm font-semibold navy-text hover:bg-navy-100"
          >
            {t.exportButton}
          </button>
        </div>

        <div className="border-t border-navy-100 pt-4 space-y-3">
          <h3 className="font-bold text-red-800">{t.deleteSectionTitle}</h3>
          <p className="text-sm text-navy-700 leading-relaxed">{t.deleteWarning}</p>
          <label className="flex items-start gap-2 text-sm text-navy-800">
            <input
              type="checkbox"
              checked={confirmDelete}
              onChange={(e) => setConfirmDelete(e.target.checked)}
              className="mt-1"
            />
            <span>{t.confirmDeleteCheckbox}</span>
          </label>
          <button
            type="button"
            disabled={!confirmDelete}
            onClick={() => void handleDeleteAccount()}
            className="rounded-md bg-red-700 px-4 py-2 text-sm font-semibold text-white hover:bg-red-800 disabled:opacity-40"
          >
            {t.deleteAccountButton}
          </button>
        </div>
      </section>
    </div>
  );
}
