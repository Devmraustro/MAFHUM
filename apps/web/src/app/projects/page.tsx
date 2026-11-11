"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import type { FormEvent } from "react";

import { ErrorAlert, FieldError } from "@/components/alerts";
import { ConfirmDialog } from "@/components/confirm-dialog";
import { ApiError, auth, documents, projects } from "@/lib/api";
import type { Project } from "@/lib/api";
import { useLocale } from "@/lib/locale-context";

/**
 * Projects: group documents for one subject and read them as a unit.
 *
 * The `nav.projects` string existed in both dictionaries long before this page
 * did, and the API could already create and delete projects — the surface was
 * simply missing. Counts are fetched from the documents endpoint per project so
 * the number shown is the real number, not a cached guess.
 */
interface ProjectWithCount extends Project {
  documentCount: number;
}

export default function ProjectsPage() {
  const { t } = useLocale();
  const router = useRouter();

  const [items, setItems] = useState<ProjectWithCount[]>([]);
  const [loading, setLoading] = useState(true);
  const [pageError, setPageError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const [formOpen, setFormOpen] = useState(false);
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [language, setLanguage] = useState("ar");
  const [creating, setCreating] = useState(false);
  const [fieldErrors, setFieldErrors] = useState<Record<string, string[]>>({});
  const [pendingDelete, setPendingDelete] = useState<ProjectWithCount | null>(null);

  /**
   * Fetch projects with their document counts. Deliberately returns data and
   * touches no state: the effect and the mutation handlers decide what to do
   * with it, so no state is written synchronously from an effect body.
   */
  const fetchProjects = useCallback(async (): Promise<ProjectWithCount[]> => {
    const list = await projects.list();
    // One count query per project: bounded by the number of projects a student
    // has, and it keeps the number honest.
    return Promise.all(
      list.items.map(async (project) => {
        try {
          const page = await documents.list({ projectId: project.id, pageSize: 1 });
          return { ...project, documentCount: page.total };
        } catch {
          return { ...project, documentCount: 0 };
        }
      }),
    );
  }, []);

  // Initial load: session, CSRF, then the projects themselves.
  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        await auth.ensureCsrf();
        const user = await auth.me();
        if (!user) {
          router.replace("/login");
          return;
        }
        const withCounts = await fetchProjects();
        if (!cancelled) setItems(withCounts);
      } catch (error) {
        if (!cancelled) {
          setPageError(error instanceof ApiError ? error.message : t("projects.error"));
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [fetchProjects, router, t]);

  async function handleCreate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setCreating(true);
    setPageError(null);
    setFieldErrors({});
    try {
      const created = await projects.create(title, language, description);
      setItems((current) => [{ ...created, documentCount: 0 }, ...current]);
      setTitle("");
      setDescription("");
      setFormOpen(false);
      setNotice(t("projects.created"));
    } catch (error) {
      if (error instanceof ApiError) {
        setFieldErrors(error.fieldErrors);
        if (Object.keys(error.fieldErrors).length === 0) setPageError(error.message);
      } else {
        setPageError(t("projects.error"));
      }
    } finally {
      setCreating(false);
    }
  }

  async function handleDelete() {
    const target = pendingDelete;
    setPendingDelete(null);
    if (!target) return;
    try {
      await projects.remove(target.id);
      setItems((current) => current.filter((item) => item.id !== target.id));
    } catch (error) {
      setPageError(error instanceof ApiError ? error.message : t("projects.error"));
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
        <div>
          <h1>{t("projects.title")}</h1>
          <p className="hint">{t("projects.subtitle")}</p>
        </div>
        <button className="btn btn-primary" type="button" onClick={() => setFormOpen((open) => !open)}>
          {t("projects.new")}
        </button>
      </header>

      {pageError ? <ErrorAlert message={pageError} onClose={() => setPageError(null)} /> : null}
      {notice ? (
        <p className="notice notice-success" role="status">
          {notice}
        </p>
      ) : null}

      {formOpen ? (
        <section className="card" aria-labelledby="new-project">
          <h2 id="new-project">{t("projects.new")}</h2>
          <form onSubmit={handleCreate} noValidate>
            <div className="field">
              <label htmlFor="project-title">{t("projects.titleField")}</label>
              <input
                id="project-title"
                required
                maxLength={200}
                placeholder={t("projects.placeholderTitle")}
                value={title}
                onChange={(event) => setTitle(event.target.value)}
                aria-invalid={Boolean(fieldErrors.title)}
              />
              <FieldError messages={fieldErrors.title} />
            </div>
            <div className="field">
              <label htmlFor="project-description">{t("projects.descriptionField")}</label>
              <textarea
                id="project-description"
                rows={3}
                maxLength={4000}
                placeholder={t("projects.placeholderDescription")}
                value={description}
                onChange={(event) => setDescription(event.target.value)}
              />
            </div>
            <div className="field">
              <label htmlFor="project-language">{t("projects.language")}</label>
              <select
                id="project-language"
                value={language}
                onChange={(event) => setLanguage(event.target.value)}
              >
                <option value="ar">العربية</option>
                <option value="fr">Français</option>
                <option value="en">English</option>
              </select>
            </div>
            <button className="btn btn-primary" type="submit" disabled={creating}>
              {creating ? (
                <>
                  <span className="spinner" aria-hidden="true" /> {t("projects.creating")}
                </>
              ) : (
                t("projects.create")
              )}
            </button>
          </form>
        </section>
      ) : null}

      {items.length === 0 ? (
        <p className="empty-state">{t("projects.empty")}</p>
      ) : (
        <ul className="project-grid">
          {items.map((project) => (
            <li key={project.id} className="card project-card">
              <h2>{project.title}</h2>
              {project.description ? <p className="hint">{project.description}</p> : null}
              <p className="project-meta">
                <span className="badge badge-info">
                  {project.documentCount} {t("projects.documents")}
                </span>
                <span className="badge">{project.language.toUpperCase()}</span>
              </p>
              <div className="row-actions">
                <Link
                  className="btn btn-sm btn-secondary"
                  href={`/documents?project=${project.id}`}
                >
                  {t("projects.viewDocuments")}
                </Link>
                <button
                  className="btn btn-sm btn-danger"
                  type="button"
                  onClick={() => setPendingDelete(project)}
                >
                  {t("projects.delete")}
                </button>
              </div>
            </li>
          ))}
        </ul>
      )}

      <ConfirmDialog
        title={t("projects.delete")}
        message={t("projects.confirmDelete")}
        confirmLabel={t("projects.delete")}
        cancelLabel={t("common.cancel")}
        onConfirm={handleDelete}
        onCancel={() => setPendingDelete(null)}
      />
    </div>
  );
}
