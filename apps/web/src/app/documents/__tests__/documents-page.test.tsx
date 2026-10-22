import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import DocumentsPage from "@/app/documents/page";

/**
 * `next/navigation` is replaced with a recording stub: the page navigates
 * through the router, and a jsdom navigation would unload the test document.
 * Everything else — the page, the API client, CSRF, error rendering — is real.
 */
const routerStub = vi.hoisted(() => ({
  push: vi.fn(),
  replace: vi.fn(),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => routerStub,
}));
import { LocaleProvider } from "@/lib/locale-context";
import { t } from "@/lib/i18n";

/**
 * Renders the real document workspace against a stubbed network.
 *
 * Only `fetch` is replaced — by a router that answers the exact routes the API
 * exposes. The component, the API client, the CSRF handling and the Arabic
 * error rendering are all the real implementation, so what these tests observe
 * is what a student would see.
 */

const USER = {
  id: "u-1",
  email: "student@example.com",
  display_name: "طالبة",
  role: "student",
  preferred_language: "ar",
};

const LIMITS = {
  max_upload_mb: 20,
  max_pdf_pages: 200,
  max_documents_per_user: 5,
  max_total_bytes_per_user_mb: 200,
  allowed_extensions: [".pdf", ".docx"],
  ocr_available: false,
    malware_scanning_available: false,
};

const USAGE = {
  documents: 2,
  total_bytes: 245_760,
  max_documents: 5,
  max_total_bytes: 209_715_200,
};

function doc(overrides: Record<string, unknown> = {}) {
  return {
    id: "d-1",
    display_filename: "بحث-التخرج.pdf",
    format: "pdf",
    size_bytes: 122_880,
    status: "ready",
    page_count: 4,
    char_count: 3120,
    segment_count: 27,
    project_id: null,
    language_hint: "ar",
    is_scanned: false,
    parser_name: "pypdf",
    parser_version: "6.21.2",
    error_code: null,
    error_message: null,
    created_at: "2026-10-20T09:00:00Z",
    processed_at: "2026-10-20T09:00:04Z",
    ...overrides,
  };
}

type Handler = (init: RequestInit) => { status?: number; body?: unknown };

/** Route table; `misses` records any path the page asks for that is unplanned,
 *  which catches the client drifting away from the real API surface. */
function routeFetch(routes: Record<string, Handler>) {
  const calls: Array<{ method: string; url: string; init: RequestInit }> = [];
  const misses: string[] = [];
  const impl = vi.fn(async (input: RequestInfo | URL, init: RequestInit = {}) => {
    const url = String(input);
    const method = (init.method ?? "GET").toUpperCase();
    calls.push({ method, url, init });
    const path = url.replace(/^\/api\/v1/, "").split("?")[0] ?? "";
    const key = `${method} ${path}`;
    const handler = routes[key];
    if (!handler) {
      misses.push(key);
      return new Response(JSON.stringify({ code: "not_found", message: "غير موجود" }), {
        status: 404,
        headers: { "Content-Type": "application/json" },
      });
    }
    const { status = 200, body = {} } = handler(init);
    return new Response(JSON.stringify(body), {
      status,
      headers: { "Content-Type": "application/json" },
    });
  });
  vi.stubGlobal("fetch", impl);
  return { calls, misses, impl };
}

const BASE_ROUTES: Record<string, Handler> = {
  "GET /auth/me": () => ({ body: USER }),
  "GET /auth/session": () => ({ body: { csrf_token: "csrf-token" } }),
  "GET /documents/limits": () => ({ body: LIMITS }),
  "GET /documents/usage": () => ({ body: USAGE }),
  "GET /projects": () => ({ body: { items: [], total: 0 } }),
};

/**
 * jsdom exposes `input.files` as a read-only accessor, so Testing Library's
 * `target: { files }` assignment silently does nothing and the submit button
 * stays disabled. The property is defined on the instance instead, which is
 * what React's onChange then reads.
 */
function attachFile(input: HTMLInputElement, file: File) {
  Object.defineProperty(input, "files", {
    value: [file],
    configurable: true,
  });
  fireEvent.change(input);
}

beforeEach(() => {
  routerStub.push.mockClear();
  routerStub.replace.mockClear();
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  document.cookie = "mafhum_csrf=; Max-Age=0; path=/";
});

function renderPage() {
  document.cookie = "mafhum_csrf=csrf-token; path=/";
  return render(
    <LocaleProvider>
      <DocumentsPage />
    </LocaleProvider>,
  );
}

describe("documents workspace", () => {
  it("renders the workspace in Arabic, right to left", async () => {
    routeFetch({
      ...BASE_ROUTES,
      "GET /documents": () => ({ body: { items: [doc()], total: 1, page: 1, page_size: 20 } }),
    });
    renderPage();

    await screen.findByText("بحث-التخرج.pdf");
    expect(document.documentElement.lang).toBe("ar");
    expect(document.documentElement.dir).toBe("rtl");
    expect(screen.getByRole("heading", { level: 1, name: t("ar", "documents.title") })).toBeTruthy();
  });

  it("shows every document the API returned and its real status", async () => {
    routeFetch({
      ...BASE_ROUTES,
      "GET /documents": () => ({
        body: {
          items: [
            doc(),
            doc({ id: "d-2", display_filename: "rapport.docx", format: "docx", status: "failed", error_code: "parse_failed", error_message: "تعذّر استخراج النص من الملف." }),
          ],
          total: 2,
          page: 1,
          page_size: 20,
        },
      }),
    });
    renderPage();

    await screen.findByText("بحث-التخرج.pdf");
    expect(screen.getByText("rapport.docx")).toBeTruthy();
    expect(screen.getByText(t("ar", "status.ready"))).toBeTruthy();
    expect(screen.getByText(t("ar", "status.failed"))).toBeTruthy();
    // The server's Arabic explanation is shown next to the failed row.
    expect(screen.getByText("تعذّر استخراج النص من الملف.")).toBeTruthy();
  });

  it("shows an explicit empty state rather than a blank area", async () => {
    routeFetch({
      ...BASE_ROUTES,
      "GET /documents": () => ({ body: { items: [], total: 0, page: 1, page_size: 20 } }),
    });
    renderPage();

    await screen.findByText(t("ar", "documents.empty"));
  });

  it("reports the server's own quota numbers instead of hardcoding them", async () => {
    routeFetch({
      ...BASE_ROUTES,
      "GET /documents": () => ({ body: { items: [doc()], total: 1, page: 1, page_size: 20 } }),
      "GET /documents/usage": () => ({
        body: { ...USAGE, documents: 3, max_documents: 7, total_bytes: 1024 },
      }),
    });
    renderPage();

    await screen.findByText("3 / 7");
    expect(screen.getByRole("progressbar")).toBeTruthy();
    expect(screen.getByRole("progressbar").getAttribute("aria-valuenow")).toBe(
      String(Math.round((3 / 7) * 100)),
    );
  });

  it("publishes the server's upload limits in the form hint", async () => {
    routeFetch({
      ...BASE_ROUTES,
      "GET /documents": () => ({ body: { items: [], total: 0, page: 1, page_size: 20 } }),
      "GET /documents/limits": () => ({ body: { ...LIMITS, max_upload_mb: 8 } }),
    });
    renderPage();

    await screen.findByText(t("ar", "documents.empty"));
    const hint = screen.getByText(/8 MB/);
    expect(hint.textContent).toContain("8 MB");
  });

  it("warns that OCR is unavailable because the server says so", async () => {
    routeFetch({
      ...BASE_ROUTES,
      "GET /documents": () => ({ body: { items: [], total: 0, page: 1, page_size: 20 } }),
    });
    renderPage();

    await screen.findByText(t("ar", "documents.empty"));
    // The scanned-PDF notice is present on the upload card, up front.
    expect(screen.getAllByText(t("ar", "detail.scanned")).length).toBeGreaterThan(0);
    // So is the disclosure that uploads are not virus-checked. A successful
    // upload must never be readable as "this file is safe".
    expect(screen.getAllByText(t("ar", "documents.noMalwareScanning")).length).toBeGreaterThan(0);
  });

  it("surfaces a validation rejection verbatim and does not fake success", async () => {
    const { calls } = routeFetch({
      ...BASE_ROUTES,
      "GET /documents": () => ({ body: { items: [], total: 0, page: 1, page_size: 20 } }),
      "POST /documents": () => ({
        status: 422,
        body: {
          code: "file_rejected",
          message: "الملف تالف أو غير مدعوم.",
          request_id: "req-9",
        },
      }),
    });
    renderPage();

    await screen.findByText(t("ar", "documents.empty"));
    const input = screen.getByLabelText(t("ar", "documents.selectFile")) as HTMLInputElement;
    attachFile(input, new File(["%PDF-1.7"], "بحث.pdf", { type: "application/pdf" }));
    // jsdom does not perform implicit submission when a submit button is
    // clicked, so the form is submitted explicitly.
    const submit = screen.getByRole("button", { name: t("ar", "documents.upload") });
    expect((submit as HTMLButtonElement).disabled).toBe(false);
    fireEvent.submit(submit.closest("form")!);

    await screen.findByRole("alert");
    expect(screen.getByRole("alert").textContent).toContain("الملف تالف أو غير مدعوم.");
    expect(calls.some((c) => c.method === "POST")).toBe(true);
    // No success banner is shown for a rejected upload.
    expect(screen.queryAllByRole("status").every((el) => !el.textContent?.includes("بحث.pdf"))).toBe(
      true,
    );
  });

  it("requires confirmation before deleting and only then calls the API", async () => {
    const { calls } = routeFetch({
      ...BASE_ROUTES,
      "GET /documents": () => ({ body: { items: [doc()], total: 1, page: 1, page_size: 20 } }),
      "DELETE /documents/d-1": () => ({ status: 204 }),
    });
    renderPage();

    await screen.findByText("بحث-التخرج.pdf");
    fireEvent.click(screen.getByRole("button", { name: t("ar", "documents.delete") }));

    // The destructive call has not happened yet.
    expect(calls.some((c) => c.method === "DELETE")).toBe(false);
    const dialog = screen.getByRole("dialog");
    expect(within(dialog).getByText(t("ar", "documents.confirmDelete"))).toBeTruthy();

    fireEvent.click(within(dialog).getByRole("button", { name: t("ar", "documents.delete") }));
    await waitFor(() => expect(calls.some((c) => c.method === "DELETE")).toBe(true));
  });

  it("cancelling the dialog performs no deletion", async () => {
    const { calls } = routeFetch({
      ...BASE_ROUTES,
      "GET /documents": () => ({ body: { items: [doc()], total: 1, page: 1, page_size: 20 } }),
      "DELETE /documents/d-1": () => ({ status: 204 }),
    });
    renderPage();

    await screen.findByText("بحث-التخرج.pdf");
    fireEvent.click(screen.getByRole("button", { name: t("ar", "documents.delete") }));
    const dialog = screen.getByRole("dialog");
    fireEvent.click(within(dialog).getByRole("button", { name: t("ar", "common.cancel") }));

    expect(screen.queryByRole("dialog")).toBeNull();
    expect(calls.some((c) => c.method === "DELETE")).toBe(false);
  });

  it("offers retry only for a failed document and re-reads the list afterwards", async () => {
    const { calls } = routeFetch({
      ...BASE_ROUTES,
      "GET /documents": () => ({
        body: {
          items: [doc({ id: "d-3", status: "failed", error_code: "parse_failed" })],
          total: 1,
          page: 1,
          page_size: 20,
        },
      }),
      "POST /documents/d-3/retry": () => ({
        body: { document_id: "d-3", job_id: "j-1", status: "pending" },
      }),
    });
    renderPage();

    await screen.findByText("بحث-التخرج.pdf");
    const listCallsBefore = calls.filter((c) => c.url.includes("/documents?")).length;

    fireEvent.click(screen.getByRole("button", { name: t("ar", "documents.retry") }));
    await waitFor(() =>
      expect(calls.some((c) => c.method === "POST" && c.url.includes("/retry"))).toBe(true),
    );
    await waitFor(() =>
      expect(calls.filter((c) => c.url.includes("/documents?")).length).toBeGreaterThan(
        listCallsBefore,
      ),
    );
  });

  it("does not offer retry for a ready document", async () => {
    routeFetch({
      ...BASE_ROUTES,
      "GET /documents": () => ({ body: { items: [doc()], total: 1, page: 1, page_size: 20 } }),
    });
    renderPage();

    await screen.findByText("بحث-التخرج.pdf");
    expect(screen.queryByRole("button", { name: t("ar", "documents.retry") })).toBeNull();
  });

  it("shows the server's Arabic message when the list request fails", async () => {
    routeFetch({
      ...BASE_ROUTES,
      "GET /documents": () => ({
        status: 503,
        body: { code: "service_unavailable", message: "الخدمة غير متاحة حاليًا." },
      }),
    });
    renderPage();

    await screen.findByRole("alert");
    expect(screen.getByRole("alert").textContent).toContain("الخدمة غير متاحة حاليًا.");
  });

  it("sends the CSRF token on mutations but never on reads", async () => {
    const { calls } = routeFetch({
      ...BASE_ROUTES,
      "GET /documents": () => ({ body: { items: [doc()], total: 1, page: 1, page_size: 20 } }),
      "DELETE /documents/d-1": () => ({ status: 204 }),
    });
    renderPage();

    await screen.findByText("بحث-التخرج.pdf");
    for (const call of calls.filter((c) => c.method === "GET")) {
      expect((call.init.headers as Record<string, string>)["X-CSRF-Token"]).toBeUndefined();
    }

    fireEvent.click(screen.getByRole("button", { name: t("ar", "documents.delete") }));
    fireEvent.click(
      within(screen.getByRole("dialog")).getByRole("button", {
        name: t("ar", "documents.delete"),
      }),
    );
    await waitFor(() => {
      const del = calls.find((c) => c.method === "DELETE");
      expect((del?.init.headers as Record<string, string>)["X-CSRF-Token"]).toBe("csrf-token");
    });
  });

  it("never asks the browser for an absolute API origin", async () => {
    const { calls } = routeFetch({
      ...BASE_ROUTES,
      "GET /documents": () => ({ body: { items: [doc()], total: 1, page: 1, page_size: 20 } }),
    });
    renderPage();

    await screen.findByText("بحث-التخرج.pdf");
    expect(calls.length).toBeGreaterThan(3);
    for (const call of calls) {
      expect(call.url.startsWith("/api/v1/"), call.url).toBe(true);
    }
  });

  it("touches no endpoint outside the documented API surface", async () => {
    const { misses } = routeFetch({
      ...BASE_ROUTES,
      "GET /documents": () => ({ body: { items: [doc()], total: 1, page: 1, page_size: 20 } }),
    });
    renderPage();

    await screen.findByText("بحث-التخرج.pdf");
    expect(misses).toEqual([]);
  });

  it("sends every request with cookies so the session is actually used", async () => {
    const { calls } = routeFetch({
      ...BASE_ROUTES,
      "GET /documents": () => ({ body: { items: [], total: 0, page: 1, page_size: 20 } }),
    });
    renderPage();

    await screen.findByText(t("ar", "documents.empty"));
    expect(calls.length).toBeGreaterThan(0);
    for (const call of calls) {
      expect(call.init.credentials).toBe("include");
    }
  });

  it("redirects to the sign-in page when the session has expired", async () => {
    routeFetch({
      ...BASE_ROUTES,
      "GET /documents": () => ({
        status: 401,
        body: { code: "unauthenticated", message: "انتهت الجلسة." },
      }),
    });
    renderPage();

    await waitFor(() => expect(routerStub.replace).toHaveBeenCalledWith("/"));
  });
});
