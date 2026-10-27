import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import DocumentDetailPage from "@/app/documents/[id]/page";
import { LocaleProvider } from "@/lib/locale-context";
import { t } from "@/lib/i18n";

/**
 * Document detail page against a stubbed network.
 *
 * `next/navigation` is mocked only to supply the route parameter; the page, the
 * API client and the Arabic rendering are the real implementation. The focus of
 * these tests is honesty: the page must show the server's real status, page
 * numbers and Arabic messages, and must not invent text the server never sent.
 */

const DOC = {
  id: "d-1",
  display_filename: "بحث-التخرج.pdf",
  format: "pdf",
  size_bytes: 122_880,
  status: "ready",
  page_count: 2,
  char_count: 950,
  segment_count: 12,
  project_id: null,
  language_hint: "ar",
  is_scanned: false,
  parser_name: "pypdf",
  parser_version: "6.21.2",
  error_code: null,
  error_message: null,
  created_at: "2026-10-20T09:00:00Z",
  processed_at: "2026-10-20T09:00:03Z",
};

const SEGMENT_TEXT_1 = "المقدمة: يهدف هذا البحث إلى دراسة المصادر.";

const SEGMENTS = [
  { ordinal: 1, page_number: 1, text: SEGMENT_TEXT_1, char_count: 45 },
  { ordinal: 2, page_number: 1, text: "الفصل الأول", char_count: 11 },
  { ordinal: 3, page_number: 2, text: "Conclusion générale du mémoire.", char_count: 31 },
];

function routeFetch(routes: Record<string, (init: RequestInit) => { status?: number; body?: unknown }>) {
  const calls: Array<{ method: string; url: string }> = [];
  const impl = vi.fn(async (input: RequestInfo | URL, init: RequestInit = {}) => {
    const url = String(input);
    const method = (init.method ?? "GET").toUpperCase();
    calls.push({ method, url });
    const path = url.replace(/^\/api\/v1/, "").split("?")[0] ?? "";
    const handler = routes[`${method} ${path}`];
    if (!handler) {
      return new Response(JSON.stringify({ code: "resource_not_found", message: "غير موجود" }), {
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
  return { calls, impl };
}

const BASE: Record<string, (init: RequestInit) => { status?: number; body?: unknown }> = {
  "GET /projects": () => ({ body: { items: [], total: 0 } }),
};

vi.mock("next/navigation", () => ({
  useParams: () => ({ id: "d-1" }),
}));

beforeEach(() => {
  document.cookie = "mafhum_csrf=csrf-token; path=/";
});

function withText() {
  routeFetch({
    ...BASE,
    "GET /documents/d-1": () => ({ body: DOC }),
    "GET /documents/d-1/text": () => ({
      body: {
        document_id: "d-1",
        status: "ready",
        items: SEGMENTS,
        total: SEGMENTS.length,
        page: 1,
        page_size: 10,
      },
    }),
  });
}

describe("document detail", () => {
  it("shows the real metadata the server returned", async () => {
    withText();
    render(
      <LocaleProvider>
        <DocumentDetailPage />
      </LocaleProvider>,
    );

    await screen.findByRole("heading", { level: 1, name: "بحث-التخرج.pdf" });
    expect(screen.getByText(t("ar", "status.ready"))).toBeTruthy();
    expect(screen.getByText("12")).toBeTruthy(); // segment_count, next to its label
    expect(screen.getByText("ar")).toBeTruthy(); // language_hint, not invented
    expect(screen.getByText(/pypdf/)).toBeTruthy(); // parser recorded for provenance
  });

  it("renders extracted text as plain text with its page number", async () => {
    withText();
    render(
      <LocaleProvider>
        <DocumentDetailPage />
      </LocaleProvider>,
    );

    const paragraph = await screen.findByText(SEGMENT_TEXT_1);
    expect(paragraph.tagName).toBe("P");
    // Page boundaries are preserved and labelled.
    expect(screen.getAllByText(`${t("ar", "detail.page")} 1`).length).toBe(2);
    expect(screen.getByText(`${t("ar", "detail.page")} 2`)).toBeTruthy();
  });

  it("never interprets document text as markup", async () => {
    routeFetch({
      ...BASE,
      "GET /documents/d-1": () => ({ body: DOC }),
      "GET /documents/d-1/text": () => ({
        body: {
          document_id: "d-1",
          status: "ready",
          items: [
            {
              ordinal: 1,
              page_number: 1,
              text: '<img src=x onerror="alert(1)"> <script>window.__pwned = true</scr' + "ipt>",
              char_count: 60,
            },
          ],
          total: 1,
          page: 1,
          page_size: 10,
        },
      }),
    });
    render(
      <LocaleProvider>
        <DocumentDetailPage />
      </LocaleProvider>,
    );

    const paragraph = await screen.findByText(/onerror/);
    // The hostile text is inert: rendered as characters, never parsed.
    expect(paragraph.textContent).toContain("<img src=x");
    expect(paragraph.querySelector("img")).toBeNull();
    expect(paragraph.querySelector("script")).toBeNull();
    expect((window as unknown as { __pwned?: boolean }).__pwned).toBeUndefined();
  });

  it("explains that a scanned PDF has no text layer and that OCR is unavailable", async () => {
    routeFetch({
      ...BASE,
      "GET /documents/d-1": () => ({ body: { ...DOC, is_scanned: true, char_count: 0, segment_count: 0 } }),
      "GET /documents/d-1/text": () => ({
        body: { document_id: "d-1", status: "ready", items: [], total: 0, page: 1, page_size: 10 },
      }),
    });
    render(
      <LocaleProvider>
        <DocumentDetailPage />
      </LocaleProvider>,
    );

    await screen.findByRole("heading", { level: 1, name: "بحث-التخرج.pdf" });
    const notice = screen.getByText(t("ar", "detail.scanned"));
    expect(notice.textContent).toContain("OCR");
    // The empty result is also stated plainly, not left as a blank panel.
    expect(screen.getByText(t("ar", "detail.empty"))).toBeTruthy();
  });

  it("shows the server's Arabic failure reason and offers a retry", async () => {
    const { calls } = routeFetch({
      ...BASE,
      "GET /documents/d-1": () => ({
        body: {
          ...DOC,
          status: "failed",
          error_code: "parse_failed",
          error_message: "تعذّر استخراج النص من الملف.",
          processed_at: null,
        },
      }),
      "POST /documents/d-1/retry": () => ({
        body: { document_id: "d-1", job_id: "j-1", status: "pending" },
      }),
    });
    render(
      <LocaleProvider>
        <DocumentDetailPage />
      </LocaleProvider>,
    );

    await screen.findByText(t("ar", "status.failed"));
    expect(screen.getByText("تعذّر استخراج النص من الملف.")).toBeTruthy();
    expect(screen.getByText("parse_failed")).toBeTruthy();
    // No text panel is fabricated for a failed document.
    expect(screen.queryByText(SEGMENT_TEXT_1)).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: t("ar", "documents.retry") }));
    await waitFor(() =>
      expect(calls.some((c) => c.method === "POST" && c.url.includes("/retry"))).toBe(true),
    );
  });

  it("says processing is underway instead of showing fake progress", async () => {
    routeFetch({
      ...BASE,
      "GET /documents/d-1": () => ({ body: { ...DOC, status: "processing", processed_at: null } }),
    });
    render(
      <LocaleProvider>
        <DocumentDetailPage />
      </LocaleProvider>,
    );

    await screen.findByText(t("ar", "status.processing"));
    expect(screen.getByText(t("ar", "detail.processing"))).toBeTruthy();
    // No percentage, no invented bar: the server reports state, not progress.
    expect(screen.queryAllByRole("progressbar")).toEqual([]);
    expect(screen.queryByText(/%/)).toBeNull();
  });

  it("links a download that the API authorizes rather than a public static path", async () => {
    withText();
    render(
      <LocaleProvider>
        <DocumentDetailPage />
      </LocaleProvider>,
    );

    const link = (await screen.findByText(t("ar", "detail.download"))).closest("a");
    expect(link?.getAttribute("href")).toBe("/api/v1/documents/d-1/download");
    // Relative, so it goes through the same authenticated origin.
    expect(link?.getAttribute("href")?.startsWith("/")).toBe(true);
  });

  it("reports the server's not-found message when the document is not the caller's", async () => {
    routeFetch({
      ...BASE,
      // The API answers 404 for someone else's document, exactly as for a
      // missing one, so the UI cannot leak existence.
      "GET /documents/d-1": () => ({
        status: 404,
        body: { code: "resource_not_found", message: "المستند غير موجود." },
      }),
    });
    render(
      <LocaleProvider>
        <DocumentDetailPage />
      </LocaleProvider>,
    );

    await screen.findByRole("alert");
    expect(screen.getByRole("alert").textContent).toContain("المستند غير موجود.");
  });
});
