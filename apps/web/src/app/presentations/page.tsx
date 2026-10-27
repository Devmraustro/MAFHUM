"use client";

import { useEffect, useState } from "react";
import type { FormEvent } from "react";

import { ErrorAlert, InfoAlert } from "@/components/alerts";
import { ApiError, presentations } from "@/lib/api";
import type { PresentationJob, PresentationStatus } from "@/lib/api";
import { useLocale } from "@/lib/locale-context";

/**
 * Presentation studio.
 *
 * The page never claims a deck is finished academic work: the draft notice is
 * always visible, and a job whose sources were deleted keeps its file but says
 * so loudly. All user-visible strings come from the shared dictionary so the
 * French locale cannot drift.
 */
export default function PresentationsPage() {
  const { t } = useLocale();
  const [title, setTitle] = useState("");
  const [question, setQuestion] = useState("");
  const [language, setLanguage] = useState<"ar" | "fr">("ar");
  const [jobs, setJobs] = useState<PresentationJob[]>([]);
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    void presentations
      .list()
      .then((list) => {
        if (!cancelled) setJobs(list.items);
      })
      .catch(() => {
        // An anonymous visitor simply sees the empty state.
        if (!cancelled) setJobs([]);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // Literal keys keep the dictionary contract statically checkable: a dynamic
  // `t(`status.${x}`)` would silently render the raw key for an unknown state.
  const statusLabels: Record<PresentationStatus, string> = {
    succeeded: t("presentations.status.succeeded"),
    failed: t("presentations.status.failed"),
    queued: t("presentations.status.queued"),
    running: t("presentations.status.running"),
  };

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (creating || title.trim().length < 1 || question.trim().length < 3) return;
    setCreating(true);
    setError(null);
    try {
      const job = await presentations.create({
        title: title.trim(),
        question: question.trim(),
        language,
      });
      setJobs((current) => [job, ...current]);
    } catch (caught) {
      if (caught instanceof ApiError && caught.status === 409) {
        setError(t("presentations.errorEvidence"));
      } else {
        setError(t("presentations.error"));
      }
    } finally {
      setCreating(false);
    }
  }

  async function handleDelete(id: string) {
    if (!window.confirm(t("presentations.confirmDelete"))) return;
    try {
      await presentations.remove(id);
      setJobs((current) => current.filter((job) => job.id !== id));
    } catch {
      setError(t("presentations.error"));
    }
  }

  return (
    <main className="container" dir="auto">
      <h1>{t("presentations.title")}</h1>
      <p className="hint">{t("presentations.subtitle")}</p>
      <InfoAlert>{t("presentations.draftNotice")}</InfoAlert>

      <form onSubmit={handleSubmit}>
        <div className="field">
          <label htmlFor="presentation-title">{t("presentations.fieldTitle")}</label>
          <input
            id="presentation-title"
            value={title}
            maxLength={200}
            placeholder={t("presentations.placeholderTitle")}
            onChange={(event) => setTitle(event.target.value)}
          />
        </div>
        <div className="field">
          <label htmlFor="presentation-question">{t("presentations.fieldQuestion")}</label>
          <textarea
            id="presentation-question"
            value={question}
            maxLength={1000}
            rows={3}
            placeholder={t("presentations.placeholderQuestion")}
            onChange={(event) => setQuestion(event.target.value)}
          />
        </div>
        <div className="field">
          <label htmlFor="presentation-language">{t("presentations.language")}</label>
          <select
            id="presentation-language"
            value={language}
            onChange={(event) => setLanguage(event.target.value === "fr" ? "fr" : "ar")}
          >
            <option value="ar">{t("presentations.langAr")}</option>
            <option value="fr">{t("presentations.langFr")}</option>
          </select>
        </div>
        <button
          type="submit"
          className="btn btn-primary"
          disabled={creating || title.trim().length < 1 || question.trim().length < 3}
        >
          {creating ? t("presentations.creating") : t("presentations.create")}
        </button>
      </form>

      {error ? <ErrorAlert message={error} onClose={() => setError(null)} /> : null}

      <section aria-labelledby="presentations-list-heading">
        <h2 id="presentations-list-heading">{t("presentations.listTitle")}</h2>
        {jobs.length === 0 ? (
          <p className="hint">{t("presentations.empty")}</p>
        ) : (
          <ul className="presentation-list" data-testid="presentation-list">
            {jobs.map((job) => (
              <li key={job.id} className="card">
                <div className="card-header">
                  <strong>{job.title}</strong>{" "}
                  <span className="badge">{statusLabels[job.status]}</span>
                </div>
                <p className="hint">
                  {job.slide_count} {t("presentations.slides")} · {job.citation_count}{" "}
                  {t("presentations.citations")}
                </p>
                {!job.sources_available ? (
                  <p className="hint" data-testid="sources-missing">
                    {t("presentations.sourcesMissing")}
                  </p>
                ) : null}
                {job.status === "failed" ? (
                  <p className="hint" data-testid="job-failed">
                    {t("presentations.failedNotice")}
                  </p>
                ) : null}
                <div className="row-actions">
                  {job.artifact ? (
                    <a
                      className="btn btn-secondary btn-sm"
                      href={presentations.downloadUrl(job.artifact.id)}
                      download={job.artifact.filename}
                    >
                      {t("presentations.download")}
                    </a>
                  ) : null}
                  <button
                    type="button"
                    className="btn btn-danger btn-sm"
                    onClick={() => void handleDelete(job.id)}
                  >
                    {t("presentations.delete")}
                  </button>
                </div>
              </li>
            ))}
          </ul>
        )}
      </section>
    </main>
  );
}
