"use client";

import { useCallback, useEffect, useState } from "react";
import type { FormEvent } from "react";

import { ErrorAlert, InfoAlert } from "@/components/alerts";
import { ApiError, research } from "@/lib/api";
import type { KnownSourceList, ResearchAnswer } from "@/lib/api";
import { useLocale } from "@/lib/locale-context";

/**
 * Evidence-grounded research.
 *
 * The answer shown here is whatever the API selected from the student's own
 * documents. There is no generated prose to display: when the evidence does not
 * support an answer, the page says so instead of showing something plausible.
 *
 * Citations are rendered as text only. A retrieved or extracted passage is
 * untrusted input, so nothing from it is interpreted as markup or script.
 */
export default function ResearchPage() {
  const { t } = useLocale();

  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState<ResearchAnswer | null>(null);
  const [loading, setLoading] = useState(false);
  const [askError, setAskError] = useState<string | null>(null);
  const [sources, setSources] = useState<KnownSourceList | null>(null);

  useEffect(() => {
    let cancelled = false;
    research
      .sources()
      .then((list) => {
        if (!cancelled) setSources(list);
      })
      .catch(() => {
        // The catalog is supplementary; a failure here must not block asking.
        if (!cancelled) setSources(null);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const handleSubmit = useCallback(
    async (event: FormEvent<HTMLFormElement>) => {
      event.preventDefault();
      const trimmed = question.trim();
      if (trimmed.length < 3 || loading) return;
      setLoading(true);
      setAskError(null);
      try {
        setAnswer(await research.ask(trimmed));
      } catch (error) {
        setAnswer(null);
        setAskError(error instanceof ApiError ? error.message : t("research.error"));
      } finally {
        setLoading(false);
      }
    },
    [question, loading, t],
  );

  const statusLabel = answer
    ? answer.status === "grounded"
      ? t("research.grounded")
      : answer.status === "insufficient_evidence"
        ? t("research.insufficient")
        : t("research.noEvidence")
    : null;

  return (
    <main className="container" dir="auto">
      <h1 id="research-heading">{t("research.title")}</h1>
      <p className="hint">{t("research.subtitle")}</p>

      <form onSubmit={handleSubmit}>
        <div className="field">
          <label htmlFor="research-question">{t("research.questionLabel")}</label>
          <textarea
            id="research-question"
            value={question}
            maxLength={1000}
            rows={3}
            placeholder={t("research.placeholder")}
            onChange={(event) => setQuestion(event.target.value)}
          />
        </div>
        <button type="submit" disabled={loading || question.trim().length < 3}>
          {loading ? t("common.loading") : t("research.ask")}
        </button>
      </form>

      {askError ? <ErrorAlert message={askError} onClose={() => setAskError(null)} /> : null}

      {answer ? (
        <section aria-labelledby="research-result-heading">
          <h2 id="research-result-heading">{statusLabel}</h2>
          {answer.status !== "grounded" ? (
            <InfoAlert>{answer.explanation}</InfoAlert>
          ) : (
            <p className="hint">{answer.explanation}</p>
          )}

          <h3>{t("research.citations")}</h3>
          {answer.citations.length === 0 ? (
            <p className="hint">{t("research.noCitations")}</p>
          ) : (
            <ol className="citations">
              {answer.citations.map((citation, index) => (
                <li key={`${citation.document_id ?? citation.source_id ?? "c"}-${index}`}>
                  {/* Rendered as text, never as markup. */}
                  <blockquote>{citation.quote}</blockquote>
                  <p className="hint">
                    {citation.locator || citation.title || t("research.noUrl")}
                    {citation.page_number === null ? ` · ${t("research.unverified")}` : ""}
                  </p>
                </li>
              ))}
            </ol>
          )}
        </section>
      ) : null}

      <section aria-labelledby="research-sources-heading">
        <h2 id="research-sources-heading">{t("research.sourcesTitle")}</h2>
        {sources ? (
          <>
            <InfoAlert>
              {sources.retrieval_enabled
                ? t("research.retrievalEnabled")
                : t("research.retrievalDisabled")}
            </InfoAlert>
            <ul>
              {sources.items.map((source) => (
                <li key={source.key}>
                  <strong>{source.title}</strong>
                  {source.institution ? ` — ${source.institution}` : ""}
                  {/* Listed is not verified, and a source with no confirmed
                      address says so rather than showing a guessed link. */}
                  {source.url ? (
                    <a href={source.url} rel="noreferrer noopener">
                      {source.url}
                    </a>
                  ) : (
                    ` · ${t("research.noUrl")}`
                  )}
                  {` · ${t("research.unverified")}`}
                </li>
              ))}
            </ul>
          </>
        ) : null}
      </section>
    </main>
  );
}
