"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";

import { ErrorAlert, InfoAlert } from "@/components/alerts";
import { StatusBadge } from "@/components/status-badge";
import { ApiError, documents, projects } from "@/lib/api";
import type { DocumentSummary, Project, TextSegment } from "@/lib/api";
import { formatBytes } from "@/lib/i18n";
import { useLocale } from "@/lib/locale-context";

const SEGMENT_PAGE_SIZE = 10;
const POLL_MS = 2500;

/**
 * Document detail: metadata, processing status, and the extracted text served
 * from the segment store in pages.
 *
 * Extracted text is rendered as plain text only. It is never parsed as markup
 * and never executed, because document content is data, not instructions.
 */
export default function DocumentDetailPage() {
  const params = useParams<{ id: string }>();
  const documentId = params?.id ?? "";
  const { locale, t } = useLocale();

  const [projectTitle, setProjectTitle] = useState<{ id: string; title: string } | null>(null);
  const [segmentPage, setSegmentPage] = useState(1);
  /**
   * Segments are stored together with the page they belong to, so "loading" is
   * *derived* rather than tracked by a flag set synchronously inside an effect,
   * which would cause a cascading render on every page change.
   */
  const [segmentResult, setSegmentResult] = useState<{
    page: number;
    items: TextSegment[];
    total: number;
  } | null>(null);
  const [document, setDocument] = useState<DocumentSummary | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  /** Bumped to force a metadata refetch: after a retry, or by the poller. */
  const [revision, setRevision] = useState(0);

  // Document metadata. State is only ever set from inside the async callback,
  // never synchronously in the effect body.
  useEffect(() => {
    if (!documentId) return;
    let cancelled = false;
    void (async () => {
      try {
        const fetched = await documents.get(documentId);
        if (!cancelled) {
          setDocument(fetched);
          setError(null);
        }
      } catch (err) {
        if (!cancelled) setError(err instanceof ApiError ? err.message : t("error.generic"));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [documentId, revision, t]);

  const projectId = document?.project_id ?? null;
  const status = document?.status;

  // The project title is resolved from the caller's own project list only, so
  // another user's project can never be named here.
  useEffect(() => {
    if (!projectId) return;
    let cancelled = false;
    void (async () => {
      try {
        const response = await projects.list();
        const match = response.items.find((item: Project) => item.id === projectId);
        if (!cancelled && match) setProjectTitle({ id: projectId, title: match.title });
      } catch {
        // A missing title is not fatal; the metadata row is simply omitted.
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [projectId]);

  // One page of extracted text, requested only once the document is ready.
  useEffect(() => {
    if (!documentId || status !== "ready") return;
    let cancelled = false;
    void (async () => {
      try {
        const response = await documents.text(documentId, segmentPage, SEGMENT_PAGE_SIZE);
        if (!cancelled) {
          setSegmentResult({ page: segmentPage, items: response.items, total: response.total });
        }
      } catch (err) {
        if (!cancelled) setError(err instanceof ApiError ? err.message : t("error.generic"));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [documentId, segmentPage, status, t]);

  // Re-read the metadata while processing is still underway. The state update
  // fires from the timer callback, not from the effect body.
  useEffect(() => {
    if (status !== "pending" && status !== "processing") return;
    const timer = setTimeout(() => setRevision((current) => current + 1), POLL_MS);
    return () => clearTimeout(timer);
  }, [status]);

  async function handleRetry() {
    if (!documentId) return;
    setBusy(true);
    setActionError(null);
    try {
      await documents.retry(documentId);
      setRevision((current) => current + 1);
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : t("error.generic"));
    } finally {
      setBusy(false);
    }
  }

  if (!document && !error) {
    return (
      <p role="status">
        <span className="spinner" aria-hidden="true" /> {t("common.loading")}
      </p>
    );
  }

  if (!document) {
    return <ErrorAlert message={error ?? t("error.generic")} />;
  }

  const segments = segmentResult?.page === segmentPage ? segmentResult.items : [];
  const totalSegments = segmentResult?.page === segmentPage ? segmentResult.total : 0;
  const segmentsLoading = segmentResult?.page !== segmentPage;
  const owningProjectTitle =
    projectTitle && projectTitle.id === document.project_id ? projectTitle.title : null;

  const localeTag = locale === "ar" ? "ar-DZ" : "fr-DZ";
  const segmentTotalPages = Math.max(1, Math.ceil(totalSegments / SEGMENT_PAGE_SIZE));
  const hasText = document.status === "ready" && document.char_count > 0;

  return (
    <>
      <p>
        <Link className="btn btn-sm btn-secondary" href="/documents">
          {t("detail.back")}
        </Link>
      </p>

      <h1>{document.display_filename}</h1>

      {error ? <ErrorAlert message={error} onClose={() => setError(null)} /> : null}
      {actionError ? (
        <ErrorAlert message={actionError} onClose={() => setActionError(null)} />
      ) : null}

      <section className="card" aria-labelledby="detail-heading">
        <div className="card-header">
          <h2 id="detail-heading">{t("detail.title")}</h2>
          <StatusBadge status={document.status} />
        </div>

        <dl className="metadata-list">
          <div>
            <dt>{t("documents.format")}</dt>
            <dd>{document.format.toUpperCase()}</dd>
          </div>
          <div>
            <dt>{t("documents.size")}</dt>
            <dd>{formatBytes(document.size_bytes, locale)}</dd>
          </div>
          <div>
            <dt>{t("documents.pages")}</dt>
            <dd>{document.page_count ?? "—"}</dd>
          </div>
          <div>
            <dt>{t("detail.characters")}</dt>
            <dd>{document.char_count}</dd>
          </div>
          <div>
            <dt>{t("detail.segments")}</dt>
            <dd>{document.segment_count}</dd>
          </div>
          <div>
            <dt>{t("detail.language")}</dt>
            <dd>{document.language_hint ?? "—"}</dd>
          </div>
          <div>
            <dt>{t("detail.uploadedAt")}</dt>
            <dd dir="ltr">
              <time dateTime={document.created_at}>
                {new Date(document.created_at).toLocaleString(localeTag)}
              </time>
            </dd>
          </div>
          {document.processed_at ? (
            <div>
              <dt>{t("detail.processedAt")}</dt>
              <dd dir="ltr">
                <time dateTime={document.processed_at}>
                  {new Date(document.processed_at).toLocaleString(localeTag)}
                </time>
              </dd>
            </div>
          ) : null}
          {document.parser_name ? (
            <div>
              <dt>{t("detail.parser")}</dt>
              <dd dir="ltr">
                <code>
                  {document.parser_name} {document.parser_version ?? ""}
                </code>
              </dd>
            </div>
          ) : null}
          {owningProjectTitle ? (
            <div>
              <dt>{t("detail.project")}</dt>
              <dd>{owningProjectTitle}</dd>
            </div>
          ) : null}
          {document.error_code ? (
            <div>
              <dt>{t("detail.errorCode")}</dt>
              <dd dir="ltr">
                <code>{document.error_code}</code>
              </dd>
            </div>
          ) : null}
          {document.error_message ? (
            <div>
              <dt>{t("detail.errorMessage")}</dt>
              <dd>{document.error_message}</dd>
            </div>
          ) : null}
        </dl>

        <div className="row-actions" style={{ marginBlockStart: "1rem" }}>
          <a className="btn btn-secondary" href={documents.downloadUrl(document.id)} download>
            {t("detail.download")}
          </a>
          {document.status === "failed" ? (
            <button
              className="btn btn-secondary"
              type="button"
              disabled={busy}
              onClick={() => void handleRetry()}
            >
              {busy ? <span className="spinner" aria-hidden="true" /> : null}
              {t("documents.retry")}
            </button>
          ) : null}
        </div>
      </section>

      {document.status === "pending" || document.status === "processing" ? (
        <InfoAlert>
          <span className="spinner" aria-hidden="true" /> {t("detail.processing")}
        </InfoAlert>
      ) : null}

      {document.status === "failed" ? (
        <InfoAlert>{t("detail.failed")}</InfoAlert>
      ) : null}

      {document.status === "ready" && document.char_count === 0 ? (
        <InfoAlert>{t("detail.empty")}</InfoAlert>
      ) : null}

      {document.status === "ready" && document.is_scanned ? (
        <InfoAlert>{t("detail.scanned")}</InfoAlert>
      ) : null}

      {hasText ? (
        <section className="card" aria-labelledby="text-heading">
          <div className="card-header">
            <h2 id="text-heading">{t("detail.text")}</h2>
            <span className="hint" aria-live="polite">
              {totalSegments} {t("detail.segments")}
            </span>
          </div>

          {segmentsLoading && segments.length === 0 ? (
            <p role="status">
              <span className="spinner" aria-hidden="true" /> {t("common.loading")}
            </p>
          ) : segments.length === 0 ? (
            <p className="empty-state">{t("detail.noSegments")}</p>
          ) : (
            <ol className="segment-list" start={(segmentPage - 1) * SEGMENT_PAGE_SIZE + 1}>
              {segments.map((segment) => (
                <li className="segment" key={segment.ordinal}>
                  <p className="segment-meta">
                    {segment.page_number != null
                      ? `${t("detail.page")} ${segment.page_number}`
                      : t("detail.pageUnknown")}
                  </p>
                  <p className="segment-text" style={{ whiteSpace: "pre-wrap" }}>{segment.text}</p>
                </li>
              ))}
            </ol>
          )}

          {segmentTotalPages > 1 ? (
            <nav
              aria-label={t("detail.text")}
              style={{ display: "flex", gap: "0.5rem", marginBlockStart: "1rem" }}
            >
              <button
                className="btn btn-sm btn-secondary"
                type="button"
                disabled={segmentPage <= 1}
                onClick={() => setSegmentPage((current) => Math.max(1, current - 1))}
              >
                ←
              </button>
              <span aria-live="polite">
                {segmentPage} / {segmentTotalPages}
              </span>
              <button
                className="btn btn-sm btn-secondary"
                type="button"
                disabled={segmentPage >= segmentTotalPages}
                onClick={() => setSegmentPage((current) => current + 1)}
              >
                →
              </button>
            </nav>
          ) : null}
        </section>
      ) : null}
    </>
  );
}
