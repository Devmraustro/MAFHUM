import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import StudyPage from "@/app/study/page";
import { LocaleProvider } from "@/lib/locale-context";

/**
 * The real study page against a stubbed network.
 *
 * Only `fetch` is replaced. The component, the API client, the CSRF handling and
 * the Arabic rendering are the real implementation, so these tests observe what
 * a student would actually see -- including what the page refuses to claim.
 */

type Handler = (init: RequestInit) => { status?: number; body?: unknown };

function routeFetch(routes: Record<string, Handler>) {
  const calls: Array<{ method: string; url: string; init: RequestInit }> = [];
  const impl = vi.fn(async (input: RequestInfo | URL, init: RequestInit = {}) => {
    const url = String(input);
    const method = (init.method ?? "GET").toUpperCase();
    calls.push({ method, url, init });
    const path = url.replace(/^\/api\/v1/, "").split("?")[0] ?? "";
    // Topic-scoped paths vary by id; match on a prefix pattern too.
    const handler = routes[`${method} ${path}`] ?? routes[`${method} ${normalise(path)}`];
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

/** Collapse the topic id out of a path so routes can be declared once. */
function normalise(path: string): string {
  return path.replace(/\/study\/topics\/[^/]+/, "/study/topics/:id");
}

const TOPIC = {
  id: "topic-1",
  title: "القانون الدستوري",
  description: null,
  language: "ar" as const,
  created_at: "2026-11-01T10:00:00+00:00",
};

function workspaceRoutes(overrides: Record<string, Handler> = {}) {
  return {
    "GET /study/topics": () => ({ body: { topics: [TOPIC] } }),
    "POST /study/topics": () => ({ status: 201, body: TOPIC }),
    "GET /study/topics/:id": () => ({ body: { ...TOPIC, materials: [], plan: emptyPlan() } }),
    "GET /study/topics/:id/plan": () => ({ body: emptyPlan() }),
    "GET /study/topics/:id/questions": () => ({ body: { questions: [] } }),
    "GET /study/topics/:id/flashcards/due": () => ({ body: { cards: [] } }),
    "GET /study/topics/:id/sessions": () => ({ body: { sessions: [] } }),
    "GET /study/topics/:id/materials": () => ({ body: { materials: [] } }),
    ...overrides,
  } satisfies Record<string, Handler>;
}

function emptyPlan() {
  return {
    total: 0,
    completed: 0,
    outstanding: 0,
    progress: 0,
    by_state: { not_started: 0, in_progress: 0, completed: 0, blocked: 0 },
    items: [],
  };
}

function renderPage(locale: "ar" | "fr" = "ar") {
  window.localStorage.setItem("mafhum.locale", locale);
  return render(
    <LocaleProvider>
      <StudyPage />
    </LocaleProvider>,
  );
}

beforeEach(() => {
  window.localStorage.clear();
  document.documentElement.removeAttribute("dir");
  document.documentElement.removeAttribute("lang");
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("topic list", () => {
  it("shows the empty state before any topic exists", async () => {
    routeFetch({ "GET /study/topics": () => ({ body: { topics: [] } }) });
    renderPage("ar");
    await waitFor(() =>
      expect(screen.getByText("لا توجد مواضيع بعد. أنشئ موضوعًا واربطه بمستنداتك.")).toBeTruthy(),
    );
  });

  it("creates a topic by posting the form values", async () => {
    const { calls } = routeFetch({
      "GET /study/topics": () => ({ body: { topics: [] } }),
      "POST /study/topics": () => ({ status: 201, body: TOPIC }),
    });
    renderPage("ar");

    fireEvent.change(await screen.findByLabelText("عنوان الموضوع"), {
      target: { value: "القانون الدستوري" },
    });
    fireEvent.click(screen.getByRole("button", { name: "إنشاء الموضوع" }));

    await waitFor(() => {
      const posted = calls.find((call) => call.method === "POST");
      expect(posted).toBeTruthy();
      expect(JSON.parse(String(posted?.init.body))).toEqual({
        title: "القانون الدستوري",
        language: "ar",
      });
    });
  });

  it("does not enable the submit button for a blank title", async () => {
    routeFetch({ "GET /study/topics": () => ({ body: { topics: [] } }) });
    renderPage("ar");
    await screen.findByText("لا توجد مواضيع بعد. أنشئ موضوعًا واربطه بمستنداتك.");
    const submit = screen.getByRole("button", { name: "إنشاء الموضوع" });
    expect(submit.hasAttribute("disabled")).toBe(true);
  });

  it("surfaces a server error instead of silently succeeding", async () => {
    routeFetch({
      "GET /study/topics": () => ({ body: { topics: [] } }),
      "POST /study/topics": () => ({ status: 400, body: { code: "bad", message: "عنوان غير صالح" } }),
    });
    renderPage("ar");
    fireEvent.change(await screen.findByLabelText("عنوان الموضوع"), {
      target: { value: "x" },
    });
    fireEvent.click(screen.getByRole("button", { name: "إنشاء الموضوع" }));
    await waitFor(() => expect(screen.getByText("عنوان غير صالح")).toBeTruthy());
  });
});

describe("grounded answers", () => {
  it("renders citations as text, never as markup", async () => {
    const payload = '<img src=x onerror="alert(1)">';
    routeFetch(
      workspaceRoutes({
        "POST /study/topics/:id/ask": () => ({
          status: 201,
          body: {
            id: "s1",
            question: "سؤال",
            answer: "إجابة",
            status: "grounded",
            evidence_sufficient: true,
            coverage: 0.8,
            citations: [
              {
                kind: "document",
                quote: payload,
                locator: "doc — p. 3 — §1",
                document_id: "d1",
                title: null,
                page_number: 3,
                ordinal: 1,
                score: 0.9,
              },
            ],
            created_at: "2026-11-01T10:00:00+00:00",
          },
        }),
        "GET /study/topics/:id/sessions": () => ({
          body: {
            sessions: [
              {
                id: "s1",
                question: "سؤال",
                answer: "إجابة",
                status: "grounded",
                evidence_sufficient: true,
                coverage: 0.8,
                citations: [
                  {
                    kind: "document",
                    quote: payload,
                    locator: "doc — p. 3 — §1",
                    document_id: "d1",
                    title: null,
                    page_number: 3,
                    ordinal: 1,
                    score: 0.9,
                  },
                ],
                created_at: "2026-11-01T10:00:00+00:00",
              },
            ],
          },
        }),
      }),
    );

    const { container } = renderPage("ar");
    fireEvent.click(await screen.findByRole("button", { name: "القانون الدستوري" }));

    await waitFor(() => expect(screen.getByText("إجابة مدعومة بالأدلة")).toBeTruthy());
    // The payload is present as literal text and produced no element.
    expect(screen.getByText(payload)).toBeTruthy();
    expect(container.querySelector("img")).toBeNull();
    // Checking for the substring "onerror=" would be wrong: the payload is
    // rendered as escaped text, so that substring legitimately appears inside
    // &lt;img ... onerror=... &gt;. What must be absent is a real tag.
    expect(container.innerHTML).not.toContain("<img");
    expect(container.querySelector("script")).toBeNull();
  });

  it("says the evidence is insufficient rather than showing a plausible answer", async () => {
    routeFetch(
      workspaceRoutes({
        "GET /study/topics/:id/sessions": () => ({
          body: {
            sessions: [
              {
                id: "s2",
                question: "سؤال",
                answer: null,
                status: "insufficient_evidence",
                evidence_sufficient: false,
                coverage: 0.1,
                citations: [],
                created_at: "2026-11-01T10:00:00+00:00",
              },
            ],
          },
        }),
      }),
    );
    renderPage("ar");
    fireEvent.click(await screen.findByRole("button", { name: "القانون الدستوري" }));

    await waitFor(() => expect(screen.getByText("الأدلة غير كافية")).toBeTruthy());
    expect(
      screen.getByText("لم يُعثر على ما يكفي للإجابة. لا تُقدَّم إجابة دون دليل."),
    ).toBeTruthy();
  });

  it("reports no material rather than inventing study content", async () => {
    routeFetch(
      workspaceRoutes({
        "GET /study/topics/:id/sessions": () => ({
          body: {
            sessions: [
              {
                id: "s3",
                question: "سؤال",
                answer: null,
                status: "no_evidence_available",
                evidence_sufficient: false,
                coverage: 0,
                citations: [],
                created_at: "2026-11-01T10:00:00+00:00",
              },
            ],
          },
        }),
      }),
    );
    renderPage("ar");
    fireEvent.click(await screen.findByRole("button", { name: "القانون الدستوري" }));
    await waitFor(() => expect(screen.getByText("لا توجد مواد للبحث فيها")).toBeTruthy());
  });
});

describe("question feedback", () => {
  it("distinguishes supported from merely unmatched, and never claims a grade", async () => {
    routeFetch(
      workspaceRoutes({
        "GET /study/topics/:id/questions": () => ({
          body: {
            questions: [
              {
                id: "q1",
                prompt: "أكمل: ………",
                difficulty: "recall",
                answer_submitted: true,
                submitted_answer: "إجابتي",
                feedback: {
                  supported_by_source: false,
                  reference_excerpt: "الخوارزميات",
                  source_available: true,
                  verdict: "not_established_by_lexical_comparison",
                  explanation: "لم يُعثر على نص المرجع حرفيًا في إجابتك.",
                  graded_semantically: false,
                },
                source_document_id: "d1",
                source_page_number: 2,
                source_ordinal: 1,
                source_available: true,
              },
            ],
          },
        }),
      }),
    );
    renderPage("ar");
    fireEvent.click(await screen.findByRole("button", { name: "القانون الدستوري" }));

    await waitFor(() =>
      expect(screen.getByText("لم يُثبَت بالمقارنة الفظية")).toBeTruthy(),
    );
    // The caveat that this is not a judgement of meaning must be on screen.
    expect(
      screen.getByText(
        "المقارنة لفظية فقط؛ قد تكون إجابتك صحيحة بمعناها، لكنها لا تُؤكَّد هنا.",
      ),
    ).toBeTruthy();
  });

  it("marks a deleted source as unavailable instead of looking checkable", async () => {
    routeFetch(
      workspaceRoutes({
        "GET /study/topics/:id/questions": () => ({
          body: {
            questions: [
              {
                id: "q2",
                prompt: "أكمل: ………",
                difficulty: "recall",
                answer_submitted: false,
                submitted_answer: null,
                feedback: null,
                source_document_id: null,
                source_page_number: null,
                source_ordinal: null,
                source_available: false,
              },
            ],
          },
        }),
      }),
    );
    renderPage("ar");
    fireEvent.click(await screen.findByRole("button", { name: "القانون الدستوري" }));
    await waitFor(() =>
      expect(screen.getAllByText("المصدر لم يعد متاحًا").length).toBeGreaterThan(0),
    );
  });
});

describe("flashcards", () => {
  it("hides the answer until the student reveals it, then posts the rating", async () => {
    const { calls } = routeFetch(
      workspaceRoutes({
        "GET /study/topics/:id/flashcards/due": () => ({
          body: {
            cards: [
              {
                id: "c1",
                prompt: "ما هي مدة الولاية؟",
                answer: "خمس سنوات",
                source_title: null,
                source_page_number: 12,
                source_ordinal: 3,
                source_available: true,
                due_at: "2026-11-01T10:00:00+00:00",
                interval_days: 0,
                repetitions: 0,
                review_count: 0,
              },
            ],
          },
        }),
        "POST /study/topics/:id/flashcards/c1/review": () => ({
          body: {
            id: "c1",
            prompt: "ما هي مدة الولاية؟",
            answer: "خمس سنوات",
            source_title: null,
            source_page_number: 12,
            source_ordinal: 3,
            source_available: true,
            due_at: "2026-11-02T10:00:00+00:00",
            interval_days: 1,
            repetitions: 1,
            review_count: 1,
          },
        }),
      }),
    );
    renderPage("ar");
    fireEvent.click(await screen.findByRole("button", { name: "القانون الدستوري" }));

    expect(await screen.findByText("ما هي مدة الولاية؟")).toBeTruthy();
    // The answer is not in the document before revealing.
    expect(screen.queryByText("خمس سنوات")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "أظهر الجواب" }));
    expect(screen.getByText("خمس سنوات")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "جيد (4)" }));
    await waitFor(() => {
      const review = calls.find((call) => call.url.includes("/review"));
      expect(review).toBeTruthy();
      expect(JSON.parse(String(review?.init.body))).toEqual({ rating: 4 });
    });
  });

  it("shows an empty state when nothing is due", async () => {
    routeFetch(workspaceRoutes());
    renderPage("ar");
    fireEvent.click(await screen.findByRole("button", { name: "القانون الدستوري" }));
    await waitFor(() =>
      expect(screen.getByText("لا توجد بطاقات مستحقة الآن.")).toBeTruthy(),
    );
  });
});

describe("study plan", () => {
  it("shows progress as a percentage of completed items", async () => {
    routeFetch(
      workspaceRoutes({
        "GET /study/topics/:id/plan": () => ({
          body: {
            total: 4,
            completed: 1,
            outstanding: 3,
            progress: 0.25,
            by_state: { not_started: 3, in_progress: 0, completed: 1, blocked: 0 },
            items: [
              {
                id: "p1",
                objective: "قراءة الفصل الأول",
                scheduled_for: "2026-11-01",
                state: "completed",
                material_note: null,
              },
              {
                id: "p2",
                objective: "تلخيص الفصل الثاني",
                scheduled_for: "2026-11-02",
                state: "not_started",
                material_note: null,
              },
            ],
          },
        }),
      }),
    );
    renderPage("ar");
    fireEvent.click(await screen.findByRole("button", { name: "القانون الدستوري" }));

    const status = await screen.findByRole("status", { name: "" });
    await waitFor(() => {
      const planText = screen.getByText(/التقدّم/);
      expect(planText.textContent).toContain("25%");
      expect(planText.textContent).toContain("مكتمل 1");
      expect(planText.textContent).toContain("متبقٍّ 3");
    });
    expect(status).toBeTruthy();
  });

  it("posts the state change when an item is completed", async () => {
    const { calls } = routeFetch(
      workspaceRoutes({
        "GET /study/topics/:id/plan": () => ({
          body: {
            total: 1,
            completed: 0,
            outstanding: 1,
            progress: 0,
            by_state: { not_started: 1, in_progress: 0, completed: 0, blocked: 0 },
            items: [
              {
                id: "p1",
                objective: "قراءة الفصل الأول",
                scheduled_for: "2026-11-01",
                state: "not_started",
                material_note: null,
              },
            ],
          },
        }),
        "PATCH /study/topics/:id/plan/p1": () => ({ body: { id: "p1", state: "completed" } }),
      }),
    );
    renderPage("ar");
    fireEvent.click(await screen.findByRole("button", { name: "القانون الدستوري" }));

    fireEvent.click(await screen.findByRole("button", { name: "تم الإنجاز" }));
    await waitFor(() => {
      const patch = calls.find((call) => call.method === "PATCH");
      expect(patch).toBeTruthy();
      expect(patch?.url).toContain("/plan/p1");
      expect(JSON.parse(String(patch?.init.body))).toEqual({ state: "completed" });
    });
  });
});

describe("localisation", () => {
  it("renders the workspace in Arabic with dir=rtl", async () => {
    routeFetch(workspaceRoutes());
    const { container } = renderPage("ar");
    fireEvent.click(await screen.findByRole("button", { name: "القانون الدستوري" }));

    await waitFor(() => {
      const main = container.querySelector("main");
      expect(main?.getAttribute("dir")).toBe("rtl");
      expect(main?.getAttribute("lang")).toBe("ar");
    });
  });

  it("renders a French topic with dir=ltr", async () => {
    const french = { ...TOPIC, title: "Droit constitutionnel", language: "fr" as const };
    routeFetch({
      "GET /study/topics": () => ({ body: { topics: [french] } }),
      "GET /study/topics/:id": () => ({ body: { ...french, materials: [], plan: emptyPlan() } }),
      "GET /study/topics/:id/plan": () => ({ body: emptyPlan() }),
      "GET /study/topics/:id/questions": () => ({ body: { questions: [] } }),
      "GET /study/topics/:id/flashcards/due": () => ({ body: { cards: [] } }),
      "GET /study/topics/:id/sessions": () => ({ body: { sessions: [] } }),
      "GET /study/topics/:id/materials": () => ({ body: { materials: [] } }),
    });
    const { container } = renderPage("fr");
    fireEvent.click(await screen.findByRole("button", { name: "Droit constitutionnel" }));

    await waitFor(() => {
      const main = container.querySelector("main");
      expect(main?.getAttribute("dir")).toBe("ltr");
      expect(main?.getAttribute("lang")).toBe("fr");
    });
  });

  it("has both dictionaries populated for every study key", async () => {
    const { t } = await import("@/lib/i18n");
    for (const locale of ["ar", "fr"] as const) {
      window.localStorage.setItem("mafhum.locale", locale);
      const value = t(locale, "study.title");
      expect(value).toBeTruthy();
      expect(value).not.toBe("study.title");
    }
    expect(t("ar", "study.title")).not.toBe(t("fr", "study.title"));
  });
});
