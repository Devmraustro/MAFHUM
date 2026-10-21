"use client";

import { FormEvent, useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useLocales } from "@/components/locale-provider";
import { api, ApiClientError } from "@/lib/api";
import type { ProjectOut, UsageOut, UserOut } from "@/lib/contract";

const PAGE_SIZE = 10;

export default function WorkspacePage() {
  const { dictionary } = useLocales();
  const t = dictionary.workspace;

  const [user, setUser] = useState<UserOut | null>(null);
  const [usage, setUsage] = useState<UsageOut | null>(null);
  const [projects, setProjects] = useState<ProjectOut[]>([]);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [loading, setLoading] = useState(true);
  const [unauthenticated, setUnauthenticated] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  const [newTitle, setNewTitle] = useState("");
  const [newDescription, setNewDescription] = useState("");
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editTitle, setEditTitle] = useState("");
  const [editDescription, setEditDescription] = useState("");

  const refreshWorkspace = useCallback(async (pageOffset: number) => {
    const [me, usageData, projectPage] = await Promise.all([
      api.getMe(),
      api.getUsage(),
      api.listProjects(PAGE_SIZE, pageOffset),
    ]);
    setUser(me);
    setUsage(usageData);
    setProjects(projectPage.items);
    setTotal(projectPage.total);
    setUnauthenticated(false);
    setErrorMessage(null);
  }, []);

  useEffect(() => {
    let cancelled = false;
    Promise.all([api.getMe(), api.getUsage(), api.listProjects(PAGE_SIZE, offset)])
      .then(([me, usageData, projectPage]) => {
        if (cancelled) return;
        setUser(me);
        setUsage(usageData);
        setProjects(projectPage.items);
        setTotal(projectPage.total);
        setUnauthenticated(false);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        if (err instanceof ApiClientError && err.status === 401) {
          setUnauthenticated(true);
          setUser(null);
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
  }, [offset]);

  const handleCreate = async (e: FormEvent) => {
    e.preventDefault();
    setErrorMessage(null);
    try {
      await api.createProject({
        title: newTitle,
        description: newDescription.trim() || null,
      });
      setNewTitle("");
      setNewDescription("");
      setOffset(0);
      await refreshWorkspace(0);
    } catch (err) {
      setErrorMessage(err instanceof Error ? err.message : "Failed to create project");
    }
  };

  const handleSaveEdit = async (id: string) => {
    setErrorMessage(null);
    try {
      await api.updateProject(id, {
        title: editTitle,
        description: editDescription.trim() || null,
      });
      setEditingId(null);
      await refreshWorkspace(offset);
    } catch (err) {
      setErrorMessage(err instanceof Error ? err.message : "Failed to update project");
    }
  };

  const handleDelete = async (id: string) => {
    setErrorMessage(null);
    try {
      await api.deleteProject(id);
      await refreshWorkspace(offset);
    } catch (err) {
      setErrorMessage(err instanceof Error ? err.message : "Failed to delete project");
    }
  };

  if (unauthenticated && !loading) {
    return (
      <div className="max-w-lg mx-auto border border-navy-200 rounded-xl bg-navy-50 p-8 space-y-4">
        <h1 className="text-2xl font-bold navy-text">{t.signInRequiredTitle}</h1>
        <p className="text-navy-700">{t.signInRequiredBody}</p>
        <Link
          href="/auth"
          className="inline-block rounded-md bg-navy px-4 py-2.5 text-sm font-semibold text-white hover:bg-navy-800 transition-colors"
        >
          {t.goToAuth}
        </Link>
      </div>
    );
  }

  return (
    <div className="space-y-8">
      <div className="flex flex-col md:flex-row md:items-center md:justify-between gap-4 border-b border-navy-100 pb-6">
        <div>
          <h1 className="text-2xl font-bold navy-text">{t.title}</h1>
          <p className="text-sm text-navy-700 mt-1">{t.subtitle}</p>
        </div>

        {usage && user && (
          <div className="rounded-lg border border-navy-200 bg-navy-50 px-4 py-3 text-sm text-navy-800">
            <span className="font-semibold">{t.quotaLabel}:</span>{" "}
            <span dir="ltr" className="font-mono font-bold">
              {usage.projects_used} / {usage.project_quota}
            </span>
          </div>
        )}
      </div>

      {errorMessage && (
        <div role="alert" className="rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-900">
          {errorMessage}
        </div>
      )}

      {/* Create project form */}
      <section className="border border-navy-200 rounded-xl bg-white p-6 space-y-4">
        <h2 className="text-lg font-bold navy-text">{t.newProjectTitle}</h2>
        <form onSubmit={handleCreate} className="space-y-4">
          <div>
            <label htmlFor="project-title" className="block text-sm font-medium navy-text mb-1">
              {t.projectTitleLabel}
            </label>
            <input
              id="project-title"
              type="text"
              required
              maxLength={200}
              value={newTitle}
              onChange={(e) => setNewTitle(e.target.value)}
              className="w-full rounded-md border border-navy-200 px-3 py-2 text-sm text-navy-900 focus:border-navy focus:outline-none"
            />
          </div>
          <div>
            <label htmlFor="project-desc" className="block text-sm font-medium navy-text mb-1">
              {t.projectDescriptionLabel}
            </label>
            <textarea
              id="project-desc"
              rows={2}
              maxLength={4000}
              value={newDescription}
              onChange={(e) => setNewDescription(e.target.value)}
              className="w-full rounded-md border border-navy-200 px-3 py-2 text-sm text-navy-900 focus:border-navy focus:outline-none"
            />
          </div>
          <button
            type="submit"
            className="rounded-md bg-navy px-4 py-2 text-sm font-semibold text-white hover:bg-navy-800 transition-colors"
          >
            {t.createButton}
          </button>
        </form>
      </section>

      {/* Projects list */}
      <section className="space-y-4">
        {projects.length === 0 && !loading ? (
          <p className="text-sm text-navy-600 border border-dashed border-navy-200 rounded-lg p-6 text-center">
            {t.emptyState}
          </p>
        ) : (
          <ul className="space-y-3 list-none">
            {projects.map((project) => (
              <li
                key={project.id}
                className="border border-navy-200 rounded-lg bg-white p-4 space-y-3"
              >
                {editingId === project.id ? (
                  <div className="space-y-3">
                    <input
                      aria-label={t.projectTitleLabel}
                      type="text"
                      maxLength={200}
                      value={editTitle}
                      onChange={(e) => setEditTitle(e.target.value)}
                      className="w-full rounded-md border border-navy-200 px-3 py-1.5 text-sm"
                    />
                    <textarea
                      aria-label={t.projectDescriptionLabel}
                      rows={2}
                      maxLength={4000}
                      value={editDescription}
                      onChange={(e) => setEditDescription(e.target.value)}
                      className="w-full rounded-md border border-navy-200 px-3 py-1.5 text-sm"
                    />
                    <div className="flex gap-2">
                      <button
                        type="button"
                        onClick={() => void handleSaveEdit(project.id)}
                        className="rounded-md bg-navy px-3 py-1.5 text-xs font-semibold text-white"
                      >
                        {t.saveButton}
                      </button>
                      <button
                        type="button"
                        onClick={() => setEditingId(null)}
                        className="rounded-md border border-navy-200 px-3 py-1.5 text-xs font-medium text-navy-700"
                      >
                        {t.cancelButton}
                      </button>
                    </div>
                  </div>
                ) : (
                  <div className="flex items-start justify-between gap-4">
                    <div className="space-y-1">
                      <h3 className="font-semibold navy-text">{project.title}</h3>
                      {project.description && (
                        <p className="text-sm text-navy-700">{project.description}</p>
                      )}
                      <p className="text-xs text-navy-500">
                        {t.updatedLabel}:{" "}
                        <span dir="ltr">{new Date(project.updated_at).toISOString().slice(0, 10)}</span>
                      </p>
                    </div>
                    <div className="flex items-center gap-2 shrink-0">
                      <button
                        type="button"
                        onClick={() => {
                          setEditingId(project.id);
                          setEditTitle(project.title);
                          setEditDescription(project.description ?? "");
                        }}
                        className="rounded-md border border-navy-200 px-3 py-1 text-xs font-medium text-navy-700 hover:bg-navy-50"
                      >
                        {t.editButton}
                      </button>
                      <button
                        type="button"
                        onClick={() => void handleDelete(project.id)}
                        className="rounded-md border border-red-200 px-3 py-1 text-xs font-medium text-red-700 hover:bg-red-50"
                      >
                        {t.deleteButton}
                      </button>
                    </div>
                  </div>
                )}
              </li>
            ))}
          </ul>
        )}

        {total > PAGE_SIZE && (
          <div className="flex items-center justify-between pt-2">
            <button
              type="button"
              disabled={offset === 0}
              onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
              className="rounded-md border border-navy-200 px-3 py-1.5 text-xs font-medium text-navy-700 disabled:opacity-40"
            >
              {t.prevPage}
            </button>
            <button
              type="button"
              disabled={offset + PAGE_SIZE >= total}
              onClick={() => setOffset(offset + PAGE_SIZE)}
              className="rounded-md border border-navy-200 px-3 py-1.5 text-xs font-medium text-navy-700 disabled:opacity-40"
            >
              {t.nextPage}
            </button>
          </div>
        )}
      </section>
    </div>
  );
}
