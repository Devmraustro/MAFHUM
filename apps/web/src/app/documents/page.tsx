"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import type { ChangeEvent, FormEvent } from "react";

import { ErrorAlert, InfoAlert } from "@/components/alerts";
import { ApiError, auth, documents, projects } from "@/lib/api";
import type { DocumentSummary, Project, UploadLimits, Usage } from "@/lib/api";
import { formatBytes } from "@/lib/i18n";
import { useLocale } from "@/lib/locale-context";
import { StatusBadge } from "@/components/status-badge";
import { ConfirmDialog } from "@/components/confirm-dialog";

const PAGE_SIZE = 20;
/** Poll interval while any document is still being processed. */
const POLL_MS = 2500;

/**
 * Document workspace: upload, list, monitor, retry and delete.
 *
 * Every list, status and error shown here comes from the API. Nothing is mocked
 * and no progress bar is faked — the status column reflects the server's real
 * processing state, refreshed by polling while work is outstanding.
 */
export default function DocumentsPage() {
  const { locale, t } = useLocale();
  const router = useRouter();

  const [items, setItems] = useState<DocumentSummary[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(true);
  const [listError, setListError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const [file, setFile] = useState<File | null>(null);
  const [projectId, setProjectId] = useState<string>("");
  const [projectList, setProjectList] = useState<Project[]>([]);
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);

  const [usage, setUsage] = useState<Usage | null>(null);
  const [limits, setLimits] = useState<UploadLimits | null>(null);
  const [pendingDelete, setPendingDelete] = useState<DocumentSummary | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);

  const pollTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const refreshList = useCallback(
    async (targetPage: number, quiet = false) => {
      if (!quiet) setLoading(true);
      try {
        const [list, currentUsage] = await Promise.all([
          documents.list({ page: targetPage, pageSize: PAGE_SIZE }),
          documents.usage(),
        ]);
        setItems(list.items);
        setTotal(list.total);
        setUsage(currentUsage);
        setListError(null);
      } catch (error) {
        if (error instanceof ApiError && error.status === 401) {
          router.replace("/");
          return;
        }
        setListError(error instanceof ApiError ? error.message : t("error.generic"));
      } finally {
        if (!quiet) setLoading(false);
      }
    },
    [router, t],
  );

  // Initial load: session, CSRF, limits, projects and the first page.
  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const user = await auth.me();
        if (!user) {
          router.replace("/");
          return;
        }
        await auth.ensureCsrf();
        const [serverLimits, projectResponse] = await Promise.all([
          documents.limits(),
          projects.list(),
        ]);
        if (cancelled) return;
        setLimits(serverLimits);
        setProjectList(projectResponse.items);
      } catch (error) {
        if (!cancelled) {
          setListError(error instanceof ApiError ? error.message : t("error.generic"));
        }
      }
      if (!cancelled) await refreshList(1);
    })();
    return () => {
      cancelled = true;
    };
  }, [refreshList, router, t]);

  // Poll only while something is still in flight.
  useEffect(() => {
    const outstanding = items.some(
      (item) => item.status === "pending" || item.status === "processing",
    );
    if (!outstanding) return;
    pollTimer.current = setTimeout(() => void refreshList(page, true), POLL_MS);
    return () => {
      if (pollTimer.current) clearTimeout(pollTimer.current);
    };
  }, [items, page, refreshList]);

  function handleFileChange(event: ChangeEvent<HTMLInputElement>) {
    setUploadError(null);
    const selected = event.target.files?.[0] ?? null;
    if (!selected) {
      setFile(null);
      return;
    }
    if (limits && selected.size > limits.max_upload_mb * 1024 * 1024) {
      setUploadError(t("documents.uploadHint"));
      setFile(null);
      event.target.value = "";
      return;
    }
    setFile(selected);
  }

  async function handleUpload(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!file) return;
    setUploading(true);
    setUploadError(null);
    setNotice(null);
    try {
      await documents.upload(file, projectId || null);
      setFile(null);
      setProjectId("");
      const input = document.getElementById("upload-file") as HTMLInputElement | null;
      if (input) input.value = "";
      await refreshList(1);
    } catch (error) {
      // The server's Arabic message explains exactly why the file was refused.
      setUploadError(error instanceof ApiError ? error.message : t("error.generic"));
    } finally {
      setUploading(false);
    }
  }

  async function handleRetry(document: DocumentSummary) {
    setActionError(null);
    setBusyId(document.id);
    try {
      await documents.retry(document.id);
      await refreshList(page, true);
    } catch (error) {
      setActionError(error instanceof ApiError ? error.message : t("error.generic"));
    } finally {
      setBusyId(null);
    }
  }

  async function handleDelete(document: DocumentSummary) {
    setActionError(null);
    setBusyId(document.id);
    try {
      await documents.remove(document.id);
      setPendingDelete(null);
      setNotice(document.display_filename);
      await refreshList(page, true);
    } catch (error) {
      setActionError(error instanceof ApiError ? error.message : t("error.generic"));
    } finally {
      setBusyId(null);
    }
  }

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const docUsagePct =
    usage && usage.max_documents > 0
      ? Math.min(100, Math.round((usage.documents / usage.max_documents) * 100))
      : 0;

  return (
    <>
      <h1>{t("documents.title")}</h1>

      {/* Announced to screen readers; not visually intrusive. */}
      <p className="visually-hidden" role="status" aria-live="polite">
        {t("a11y.statusAnnouncer")}: {total}
      </p>

      {listError ? <ErrorAlert message={listError} onClose={() => setListError(null)} /> : null}
      {actionError ? <ErrorAlert message={actionError} onClose={() => setActionError(null)} /> : null}
      {notice ? (
        <InfoAlert>
          {t("documents.delete")}: {notice}
        </InfoAlert>
      ) : null}

      <section className="card" aria-labelledby="upload-heading">
        <h2 id="upload-heading">{t("documents.upload")}</h2>
        <p className="hint">
          {t("documents.uploadHint")}
          {limits
            ? ` ${limits.max_upload_mb} MB — ${limits.allowed_extensions.join("، ")}.`
            : ""}
        </p>
        {limits && !limits.ocr_available ? (
          <p className="hint">{t("detail.scanned")}</p>
        ) : null}
        {limits && !limits.malware_scanning_available ? (
          <p className="hint">{t("documents.noMalwareScanning")}</p>
        ) : null}

        {uploadError ? <ErrorAlert message={uploadError} onClose={() => setUploadError(null)} /> : null}

        <form onSubmit={handleUpload}>
          <div className="field">
            <label htmlFor="upload-file">{t("documents.selectFile")}</label>
            <input
              id="upload-file"
              name="file"
              type="file"
              accept={limits ? limits.allowed_extensions.join(",") : ".pdf,.docx"}
              required
              onChange={handleFileChange}
            />
          </div>

          <div className="field">
            <label htmlFor="project-select">{t("documents.attachToProject")}</label>
            <select
              id="project-select"
              name="project_id"
              value={projectId}
              onChange={(event) => setProjectId(event.target.value)}
            >
              <option value="">{t("documents.noProject")}</option>
              {projectList.map((project) => (
                <option key={project.id} value={project.id}>
                  {project.title}
                </option>
              ))}
            </select>
          </div>

          <button className="btn btn-primary" type="submit" disabled={!file || uploading}>
            {uploading ? (
              <>
                <span className="spinner" aria-hidden="true" /> {t("documents.uploading")}
              </>
            ) : (
              t("documents.upload")
            )}
          </button>
        </form>
      </section>

      {usage ? (
        <section className="card" aria-labelledby="usage-heading">
          <div className="card-header">
            <h2 id="usage-heading">{t("quota.title")}</h2>
          </div>
          <dl className="metadata-list">
            <div>
              <dt>{t("quota.documents")}</dt>
              <dd>
                {usage.documents} / {usage.max_documents}
              </dd>
            </div>
            <div>
              <dt>{t("quota.storage")}</dt>
              <dd>{formatBytes(usage.total_bytes, locale)}</dd>
            </div>
          </dl>
          <div
            className="meter"
            role="progressbar"
            aria-valuenow={docUsagePct}
            aria-valuemin={0}
            aria-valuemax={100}
            aria-label={t("quota.documents")}
            style={{ marginBlockStart: "0.75rem" }}
          >
            <div className="meter-fill" style={{ inlineSize: `${docUsagePct}%` }} />
          </div>
        </section>
      ) : null}

      <section className="card" aria-labelledby="list-heading">
        <div className="card-header">
          <h2 id="list-heading">{t("documents.title")}</h2>
          <button
            className="btn btn-sm btn-secondary"
            type="button"
            onClick={() => void refreshList(page)}
          >
            {t("documents.refresh")}
          </button>
        </div>

        {loading ? (
          <p role="status">
            <span className="spinner" aria-hidden="true" /> {t("common.loading")}
          </p>
        ) : items.length === 0 ? (
          <p className="empty-state">{t("documents.empty")}</p>
        ) : (
          <div className="table-wrap">
            <table>
              <caption className="visually-hidden">{t("a11y.documentsTable")}</caption>
              <thead>
                <tr>
                  <th scope="col">{t("documents.name")}</th>
                  <th scope="col">{t("documents.format")}</th>
                  <th scope="col">{t("documents.size")}</th>
                  <th scope="col">{t("documents.status")}</th>
                  <th scope="col">{t("documents.pages")}</th>
                  <th scope="col">
                    <span className="visually-hidden">إجراءات</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {items.map((document) => (
                  <tr key={document.id}>
                    <td>{document.display_filename}</td>
                    <td>{document.format.toUpperCase()}</td>
                    <td>{formatBytes(document.size_bytes, locale)}</td>
                    <td>
                      <StatusBadge status={document.status} />
                      {document.status === "failed" && document.error_message ? (
                        <span className="hint" style={{ display: "block" }}>
                          {document.error_message}
                        </span>
                      ) : null}
                    </td>
                    <td>{document.page_count ?? "—"}</td>
                    <td>
                      <div className="row-actions">
                        <Link
                          className="btn btn-sm btn-secondary"
                          href={`/documents/${document.id}`}
                        >
                          {t("documents.open")}
                        </Link>
                        {document.status === "failed" ? (
                          <button
                            className="btn btn-sm btn-secondary"
                            type="button"
                            disabled={busyId === document.id}
                            onClick={() => void handleRetry(document)}
                          >
                            {busyId === document.id ? (
                              <span className="spinner" aria-hidden="true" />
                            ) : null}
                            {t("documents.retry")}
                          </button>
                        ) : null}
                        <button
                          className="btn btn-sm btn-danger"
                          type="button"
                          disabled={busyId === document.id}
                          onClick={() => setPendingDelete(document)}
                        >
                          {t("documents.delete")}
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {totalPages > 1 ? (
          <nav
            aria-label="تصفح الصفحات"
            style={{ display: "flex", gap: "0.5rem", marginBlockStart: "1rem" }}
          >
            <button
              className="btn btn-sm btn-secondary"
              type="button"
              disabled={page <= 1}
              onClick={() => {
                const next = page - 1;
                setPage(next);
                void refreshList(next);
              }}
            >
              ←
            </button>
            <span aria-live="polite">
              {page} / {totalPages}
            </span>
            <button
              className="btn btn-sm btn-secondary"
              type="button"
              disabled={page >= totalPages}
              onClick={() => {
                const next = page + 1;
                setPage(next);
                void refreshList(next);
              }}
            >
              →
            </button>
          </nav>
        ) : null}
      </section>

      {pendingDelete ? (
        <ConfirmDialog
          title={pendingDelete.display_filename}
          message={t("documents.confirmDelete")}
          confirmLabel={t("documents.delete")}
          cancelLabel={t("common.cancel")}
          busy={busyId === pendingDelete.id}
          onConfirm={() => void handleDelete(pendingDelete)}
          onCancel={() => setPendingDelete(null)}
        />
      ) : null}
    </>
  );
}
