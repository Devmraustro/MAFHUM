import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import ProjectsPage from "@/app/projects/page";
import { LocaleProvider } from "@/lib/locale-context";
import { t } from "@/lib/i18n";

/**
 * Projects page: the surface that was missing while `nav.projects` already
 * existed in both dictionaries. These tests pin the behaviour a student relies
 * on — real document counts, creation, and a confirmed delete.
 */
const routerStub = vi.hoisted(() => ({ push: vi.fn(), replace: vi.fn() }));

vi.mock("next/navigation", () => ({ useRouter: () => routerStub }));

const USER = {
  id: "u-1",
  email: "student@example.com",
  display_name: "طالبة",
  role: "student",
  preferred_language: "ar",
};

interface Call {
  method: string;
  path: string;
  body: unknown;
  query?: string;
}

function jsonResponse(status: number, payload: unknown): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    text: async () => JSON.stringify(payload),
  } as unknown as Response;
}

function routeFetch(routes: Record<string, (call: Call) => Response>) {
  const calls: Call[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input.toString();
      const path = url.replace("/api/v1", "");
      const method = init?.method ?? "GET";
      const call: Call = {
        method,
        path,
        body: typeof init?.body === "string" ? JSON.parse(init.body) : null,
      };
      calls.push(call);
      const [base, query] = path.split("?");
      const handler = routes[`${method} ${base}`];
      if (!handler) {
        return jsonResponse(404, { code: "not_found", message: `no route: ${method} ${path}` });
      }
      // `query` is captured on the call so a test can assert that a count was
      // scoped to one project rather than fetched for all documents.
      call.query = query ?? "";
      return handler(call);
    }),
  );
  return calls;
}

function renderPage() {
  return render(
    <LocaleProvider>
      <ProjectsPage />
    </LocaleProvider>,
  );
}

const PROJECT = {
  id: "p-1",
  title: "القانون الدستوري",
  description: "السداسي الأول",
  language: "ar",
  created_at: "2026-11-01T10:00:00Z",
  updated_at: "2026-11-01T10:00:00Z",
};

const BASE = {
  "GET /auth/session": () => jsonResponse(200, { csrf_token: "csrf-1" }),
  "GET /auth/me": () => jsonResponse(200, USER),
};

describe("projects page", () => {
  beforeEach(() => {
    routerStub.push.mockClear();
    routerStub.replace.mockClear();
    document.cookie = "mafhum_csrf=csrf-1";
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("shows a real document count per project", async () => {
    routeFetch({
      ...BASE,
      "GET /projects": () => jsonResponse(200, { items: [PROJECT], total: 1 }),
      "GET /documents": () =>
        jsonResponse(200, { items: [], total: 7, page: 1, page_size: 1 }),
    });
    renderPage();

    expect(await screen.findByRole("heading", { name: PROJECT.title })).toBeTruthy();
    expect(screen.getByText(`7 ${t("ar", "projects.documents")}`)).toBeTruthy();
  });

  it("creates a project and appends it without a reload", async () => {
    const calls = routeFetch({
      ...BASE,
      "GET /projects": () => jsonResponse(200, { items: [], total: 0 }),
      "POST /projects": () =>
        jsonResponse(201, {
          ...PROJECT,
          id: "p-2",
          title: "مشروع جديد",
        }),
    });
    renderPage();

    await screen.findByText(t("ar", "projects.empty"));
    fireEvent.click(screen.getByRole("button", { name: t("ar", "projects.new") }));
    fireEvent.change(screen.getByLabelText(t("ar", "projects.titleField")), {
      target: { value: "مشروع جديد" },
    });
    fireEvent.click(screen.getByRole("button", { name: t("ar", "projects.create") }));

    expect(await screen.findByRole("heading", { name: "مشروع جديد" })).toBeTruthy();
    const post = calls.find((call) => call.method === "POST");
    expect(post?.body).toMatchObject({ title: "مشروع جديد", language: "ar" });
  });

  it("asks for confirmation before deleting", async () => {
    let deleted = false;
    routeFetch({
      ...BASE,
      "GET /projects": () => jsonResponse(200, { items: [PROJECT], total: 1 }),
      "GET /documents": () => jsonResponse(200, { items: [], total: 0, page: 1, page_size: 1 }),
      "DELETE /projects/p-1": () => {
        deleted = true;
        return { ok: true, status: 204, text: async () => "" } as unknown as Response;
      },
    });
    renderPage();

    fireEvent.click(await screen.findByRole("button", { name: t("ar", "projects.delete") }));
    // Nothing may be deleted merely by clicking the row action.
    expect(deleted).toBe(false);

    const dialog = await screen.findByRole("dialog");
    fireEvent.click(
      Array.from(dialog.querySelectorAll("button")).find(
        (button) => button.textContent === t("ar", "projects.delete"),
      ) as HTMLElement,
    );
    await waitFor(() => expect(deleted).toBe(true));
  });

  it("links a project to its filtered document list", async () => {
    routeFetch({
      ...BASE,
      "GET /projects": () => jsonResponse(200, { items: [PROJECT], total: 1 }),
      "GET /documents": () => jsonResponse(200, { items: [], total: 0, page: 1, page_size: 1 }),
    });
    renderPage();

    const link = await screen.findByRole("link", { name: t("ar", "projects.viewDocuments") });
    expect(link.getAttribute("href")).toBe("/documents?project=p-1");
  });
});
