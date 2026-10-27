import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import PresentationsPage from "@/app/presentations/page";
import { LocaleProvider } from "@/lib/locale-context";

/**
 * The real page against a stubbed network boundary. The draft notice, the
 * unavailable-source warning and the refusal to invent evidence are all
 * observable here, not just documented.
 */

type Handler = (init: RequestInit) => { status?: number; body?: unknown };

function routeFetch(routes: Record<string, Handler>) {
  const calls: Array<{ method: string; url: string; init: RequestInit }> = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init: RequestInit = {}) => {
      const url = String(input);
      const method = (init.method ?? "GET").toUpperCase();
      calls.push({ method, url, init });
      const path = url.replace(/^\/api\/v1/, "").split("?")[0] ?? "";
      const handler = routes[`${method} ${path}`];
      if (!handler) {
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
    }),
  );
  return calls;
}

const JOB = {
  id: "job-1",
  title: "عرض الدستور",
  question: "ما هي مدة الولاية؟",
  language: "ar" as const,
  status: "succeeded" as const,
  slide_count: 5,
  citation_count: 1,
  sources_available: true,
  citations: [{ index: 1, locator: "doc.pdf — p. 1", title: "doc.pdf", page_number: 1, available: true }],
  generator: "python-pptx",
  error_code: null,
  created_at: "2026-10-27T12:00:00Z",
  artifact: { id: "artifact-1", filename: "mafhum-deck.pptx", size_bytes: 43027 },
};

function renderPage(locale: "ar" | "fr" = "ar") {
  window.localStorage.setItem("mafhum.locale", locale);
  return render(
    <LocaleProvider>
      <PresentationsPage />
    </LocaleProvider>,
  );
}

beforeEach(() => {
  window.localStorage.clear();
  routeFetch({ "GET /presentations/jobs": () => ({ body: { items: [], total: 0 } }) });
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("presentations page", () => {
  it("always shows the draft notice and an empty state", async () => {
    renderPage();
    expect(screen.getByRole("heading", { level: 1 }).textContent).toContain("عروض");
    expect(screen.getByText(/مسودة للمراجعة/)).toBeTruthy();
    await waitFor(() => expect(screen.getByText(/لم تنشئ أي عرض بعد/)).toBeTruthy());
  });

  it("creates a deck and exposes a same-origin, editable PPTX download", async () => {
    const calls = routeFetch({
      "GET /presentations/jobs": () => ({ body: { items: [], total: 0 } }),
      "POST /presentations/jobs": () => ({ status: 201, body: JOB }),
    });
    renderPage();
    fireEvent.change(screen.getByLabelText(/عنوان العرض/), { target: { value: "عرض الدستور" } });
    fireEvent.change(screen.getByLabelText(/سؤال البحث/), {
      target: { value: "ما هي مدة الولاية؟" },
    });
    fireEvent.click(screen.getByRole("button", { name: /أنشئ العرض/ }));

    await waitFor(() => expect(screen.getByTestId("presentation-list")).toBeTruthy());
    const link = screen.getByRole("link", { name: /تنزيل PPTX/ });
    expect(link.getAttribute("href")).toBe("/api/v1/artifacts/artifact-1/download");
    expect(screen.queryByTestId("sources-missing")).toBeNull();

    const create = calls.find((call) => call.method === "POST");
    expect(create?.url).toBe("/api/v1/presentations/jobs");
    const payload = JSON.parse(String(create?.init.body));
    expect(payload).toMatchObject({ title: "عرض الدستور", language: "ar" });
  });

  it("explains a lack of evidence instead of showing a fabricated deck", async () => {
    routeFetch({
      "GET /presentations/jobs": () => ({ body: { items: [], total: 0 } }),
      "POST /presentations/jobs": () => ({
        status: 409,
        body: { code: "insufficient_evidence", message: "لا توجد أدلة" },
      }),
    });
    renderPage();
    fireEvent.change(screen.getByLabelText(/عنوان العرض/), { target: { value: "عرض" } });
    fireEvent.change(screen.getByLabelText(/سؤال البحث/), { target: { value: "سؤال بلا دليل" } });
    fireEvent.click(screen.getByRole("button", { name: /أنشئ العرض/ }));
    await waitFor(() => expect(screen.getByRole("alert").textContent).toContain("أدلة"));
  });

  it("flags a deck whose source has been deleted", async () => {
    routeFetch({
      "GET /presentations/jobs": () => ({
        body: { items: [{ ...JOB, sources_available: false }], total: 1 },
      }),
    });
    renderPage();
    await waitFor(() => expect(screen.getByTestId("sources-missing")).toBeTruthy());
    // The download is still available: the snapshot is not silently removed.
    expect(screen.getByRole("link", { name: /تنزيل PPTX/ })).toBeTruthy();
  });

  it("deletes only after confirmation", async () => {
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    const calls = routeFetch({
      "GET /presentations/jobs": () => ({ body: { items: [JOB], total: 1 } }),
      "DELETE /presentations/jobs/job-1": () => ({ status: 200, body: { deleted: true } }),
    });
    renderPage();
    await waitFor(() => expect(screen.getByTestId("presentation-list")).toBeTruthy());
    fireEvent.click(screen.getByRole("button", { name: /حذف/ }));
    await waitFor(() => expect(calls.some((call) => call.method === "DELETE")).toBe(true));
    expect(confirm).toHaveBeenCalled();
    await waitFor(() => expect(screen.getByText(/لم تنشئ أي عرض بعد/)).toBeTruthy());
  });

  it("renders the French locale", async () => {
    renderPage("fr");
    expect(screen.getByRole("heading", { level: 1 }).textContent).toContain("Présentations");
    expect(screen.getByText(/brouillon/i)).toBeTruthy();
    // Await the list promise so its state update does not outlive the test.
    await waitFor(() => expect(screen.getByText(/Aucune présentation/)).toBeTruthy());
  });
});
