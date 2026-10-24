import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import ResearchPage from "@/app/research/page";
import { LocaleProvider } from "@/lib/locale-context";
import { t } from "@/lib/i18n";

/**
 * Saving a research result from the page into a study topic.
 *
 * Only `fetch` is replaced; the component, the API client and the Arabic
 * rendering are real. What matters here is the boundary the backend enforces and
 * the UI must not paper over: a result the evidence does not support is not
 * offered for saving, and a duplicate save is reported as already-there rather
 * than shown as a new one.
 */

type Handler = (init: RequestInit) => { status?: number; body?: unknown };

function routeFetch(routes: Record<string, Handler>) {
  const calls: Array<{ method: string; url: string; init: RequestInit }> = [];
  const impl = vi.fn(async (input: RequestInfo | URL, init: RequestInit = {}) => {
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
  });
  vi.stubGlobal("fetch", impl);
  return { calls, impl };
}

const TOPICS = {
  topics: [
    { id: "topic-1", title: "القانون الدستوري", description: null, language: "ar", created_at: "" },
    { id: "topic-2", title: "Droit civil", description: null, language: "fr", created_at: "" },
  ],
};

const GROUNDED = {
  question: "ما هي مدة الولاية؟",
  language: "ar",
  status: "grounded",
  evidence_sufficient: true,
  coverage: 0.67,
  explanation: "الإجابة مبنية حصريًا على المقاطع المقتبسة.",
  citations: [
    {
      kind: "document",
      quote: "تحدد مدة ولاية رئيس الجمهورية بخمس سنوات.",
      document_id: "doc-1",
      title: "doc.pdf",
      page_number: 12,
      ordinal: 3,
      locator: "doc.pdf — p. 12 — §3",
      score: 0.67,
    },
  ],
};

const INSUFFICIENT = {
  ...GROUNDED,
  status: "insufficient_evidence",
  evidence_sufficient: false,
  coverage: 0.05,
  citations: [],
  explanation: "الأدلة غير كافية.",
};

function renderPage() {
  return render(
    <LocaleProvider>
      <ResearchPage />
    </LocaleProvider>
  );
}

async function ask(question = "ما هي مدة الولاية؟") {
  fireEvent.change(screen.getByLabelText(t("ar", "research.questionLabel")), {
    target: { value: question },
  });
  fireEvent.click(screen.getByRole("button", { name: t("ar", "research.ask") }));
  await waitFor(() => {
    expect(screen.getByTestId("save-panel")).toBeTruthy();
  });
}

describe("saving a research result into a study topic", () => {
  beforeEach(() => {
    document.cookie = "mafhum_csrf=token-ar; path=/; SameSite=Lax";
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    document.cookie = "mafhum_csrf=; path=/; expires=Thu, 01 Jan 1970 00:00:00 GMT";
  });

  it("offers the student's own topics to save into", async () => {
    routeFetch({
      "GET /study/topics": () => ({ body: TOPICS }),
      "POST /research/ask": () => ({ body: GROUNDED }),
    });
    renderPage();
    await ask();

    const select = screen.getByLabelText(t("ar", "research.saveToTopic"));
    const options = Array.from(select.querySelectorAll("option")).map((option) => option.value);
    expect(options).toEqual(["", "topic-1", "topic-2"]);
    expect(select.textContent).toContain("القانون الدستوري");
  });

  it("saves a grounded result with its citations and document ids", async () => {
    const { calls } = routeFetch({
      "GET /study/topics": () => ({ body: TOPICS }),
      "POST /research/ask": () => ({ body: GROUNDED }),
      "POST /study/topics/topic-1/research": () => ({
        status: 201,
        body: { ...GROUNDED, id: "saved-1", topic_id: "topic-1", deduplicated: false },
      }),
    });
    renderPage();
    await ask();

    fireEvent.change(screen.getByLabelText(t("ar", "research.saveToTopic")), {
      target: { value: "topic-1" },
    });
    fireEvent.click(screen.getByRole("button", { name: t("ar", "research.save") }));

    await waitFor(() => {
      expect(screen.getByTestId("save-ok").textContent).toBe(t("ar", "research.saved"));
    });

    const save = calls.find(
      (call) => call.method === "POST" && call.url.includes("/study/topics/")
    );
    expect(save).toBeTruthy();
    expect(save!.url).toBe("/api/v1/study/topics/topic-1/research");
    const payload = JSON.parse(String(save!.init.body));
    expect(payload.status).toBe("grounded");
    expect(payload.source_document_ids).toEqual(["doc-1"]);
    expect(payload.citations[0].quote).toContain("خمس سنوات");
    // The CSRF header must ride along, or the backend rejects the save.
    expect(save!.init.headers).toMatchObject({ "X-CSRF-Token": "token-ar" });
  });

  it("reports a duplicate save instead of pretending it is new", async () => {
    routeFetch({
      "GET /study/topics": () => ({ body: TOPICS }),
      "POST /research/ask": () => ({ body: GROUNDED }),
      "POST /study/topics/topic-1/research": () => ({
        status: 201,
        body: { ...GROUNDED, id: "saved-1", topic_id: "topic-1", deduplicated: true },
      }),
    });
    renderPage();
    await ask();

    fireEvent.change(screen.getByLabelText(t("ar", "research.saveToTopic")), {
      target: { value: "topic-1" },
    });
    fireEvent.click(screen.getByRole("button", { name: t("ar", "research.save") }));

    await waitFor(() => {
      expect(screen.getByTestId("save-duplicate").textContent).toBe(
        t("ar", "research.saveDuplicate")
      );
    });
    expect(screen.queryByTestId("save-ok")).toBeNull();
  });

  it("does not offer to save a result the evidence does not support", async () => {
    routeFetch({
      "GET /study/topics": () => ({ body: TOPICS }),
      "POST /research/ask": () => ({ body: INSUFFICIENT }),
    });
    renderPage();
    await ask("سؤال بلا دليل");

    expect(screen.getByTestId("save-blocked").textContent).toBe(
      t("ar", "research.unsupportedStatus")
    );
    expect(screen.queryByLabelText(t("ar", "research.saveToTopic"))).toBeNull();
  });

  it("keeps the save control disabled until a topic is chosen", async () => {
    const { calls } = routeFetch({
      "GET /study/topics": () => ({ body: TOPICS }),
      "POST /research/ask": () => ({ body: GROUNDED }),
    });
    renderPage();
    await ask();

    const button = screen.getByRole("button", { name: t("ar", "research.save") });
    expect(button.hasAttribute("disabled")).toBe(true);

    // And clicking it must not fire a request: an enabled-looking control that
    // does nothing is the failure this assertion exists to catch.
    fireEvent.click(button);
    expect(
      calls.some((call) => call.method === "POST" && call.url.includes("/study/topics/"))
    ).toBe(false);

    fireEvent.change(screen.getByLabelText(t("ar", "research.saveToTopic")), {
      target: { value: "topic-2" },
    });
    expect(button.hasAttribute("disabled")).toBe(false);
  });

  it("hides the control when the student has no topics", async () => {
    routeFetch({
      "GET /study/topics": () => ({ body: { topics: [] } }),
      "POST /research/ask": () => ({ body: GROUNDED }),
    });
    renderPage();
    await ask();

    expect(screen.queryByLabelText(t("ar", "research.saveToTopic"))).toBeNull();
    expect(screen.getByTestId("save-panel").textContent).toBe(t("ar", "research.chooseTopic"));
  });

  it("surfaces a save failure rather than silently succeeding", async () => {
    routeFetch({
      "GET /study/topics": () => ({ body: TOPICS }),
      "POST /research/ask": () => ({ body: GROUNDED }),
      "POST /study/topics/topic-1/research": () => ({
        status: 500,
        body: { code: "internal_error", message: "فشل" },
      }),
    });
    renderPage();
    await ask();

    fireEvent.change(screen.getByLabelText(t("ar", "research.saveToTopic")), {
      target: { value: "topic-1" },
    });
    fireEvent.click(screen.getByRole("button", { name: t("ar", "research.save") }));

    await waitFor(() => {
      expect(screen.getByText(t("ar", "research.saveError"))).toBeTruthy();
    });
    expect(screen.queryByTestId("save-ok")).toBeNull();
  });
});
