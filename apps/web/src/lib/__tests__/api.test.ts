import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { API_PREFIX, ApiError, auth, documents, readCsrfToken, request } from "@/lib/api";

/**
 * Tests for the real API client.
 *
 * Only `fetch` — the network boundary — is replaced, by a recorder that returns
 * canned responses. Everything asserted below is the client's own behaviour:
 * URL construction, cookie policy, CSRF handling and error normalisation.
 */

interface Recorded {
  url: string;
  init: RequestInit;
}

function stubFetch(handler: (input: RequestInfo | URL, init?: RequestInit) => Response | Promise<Response>) {
  const calls: Recorded[] = [];
  const impl = vi.fn(async (input: RequestInfo | URL, init: RequestInit = {}) => {
    calls.push({ url: String(input), init });
    return handler(input, init);
  });
  vi.stubGlobal("fetch", impl);
  return { calls, impl };
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

/** jsdom's document.cookie cannot set HttpOnly cookies, which is exactly what
 *  the session cookie is; the CSRF cookie is script-readable by design. */
function setCsrfCookie(value: string | null) {
  if (value === null) {
    document.cookie = "mafhum_csrf=; Max-Age=0; path=/";
    return;
  }
  document.cookie = `mafhum_csrf=${value}; path=/`;
}

beforeEach(() => {
  setCsrfCookie(null);
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  setCsrfCookie(null);
});

describe("readCsrfToken", () => {
  it("returns null when the cookie is absent", () => {
    expect(readCsrfToken()).toBeNull();
  });

  it("reads and decodes the double-submit cookie", () => {
    setCsrfCookie("abc123%3D%3D");
    expect(readCsrfToken()).toBe("abc123==");
  });
});

describe("request", () => {
  it("prefixes every path with the versioned API root and stays relative", async () => {
    const { calls } = stubFetch(() => jsonResponse({}));
    await request("/documents");
    expect(calls[0]?.url).toBe(`${API_PREFIX}/documents`);
    expect(calls[0]?.url.startsWith("http")).toBe(false);
  });

  it("always sends credentials and never caches", async () => {
    const { calls } = stubFetch(() => jsonResponse({}));
    await request("/auth/me");
    expect(calls[0]?.init.credentials).toBe("include");
    expect(calls[0]?.init.cache).toBe("no-store");
  });

  it("sends no CSRF header on a safe GET", async () => {
    const { calls } = stubFetch(() => jsonResponse({}));
    setCsrfCookie("tok");
    await request("/documents");
    const headers = calls[0]?.init.headers as Record<string, string>;
    expect(headers["X-CSRF-Token"]).toBeUndefined();
  });

  it("echoes the CSRF cookie on a mutation", async () => {
    const { calls } = stubFetch(() => jsonResponse({}));
    setCsrfCookie("tok-value");
    await request("/documents", { method: "POST", body: new FormData() });
    const headers = calls[0]?.init.headers as Record<string, string>;
    expect(headers["X-CSRF-Token"]).toBe("tok-value");
  });

  it("picks up a CSRF token issued after module load", async () => {
    const { calls } = stubFetch(() => jsonResponse({}));
    setCsrfCookie(null);
    await request("/auth/logout", { method: "POST" });
    expect((calls[0]?.init.headers as Record<string, string>)["X-CSRF-Token"]).toBeUndefined();

    setCsrfCookie("issued-later");
    await request("/auth/logout", { method: "POST" });
    expect((calls[1]?.init.headers as Record<string, string>)["X-CSRF-Token"]).toBe(
      "issued-later",
    );
  });

  it("does not set Content-Type for multipart so the browser owns the boundary", async () => {
    const { calls } = stubFetch(() => jsonResponse({}));
    const form = new FormData();
    form.append("file", new File(["x"], "a.pdf"));
    await request("/documents", { method: "POST", body: form });
    const headers = calls[0]?.init.headers as Record<string, string>;
    expect(headers["Content-Type"]).toBeUndefined();
  });

  it("sets a JSON Content-Type for a JSON body", async () => {
    const { calls } = stubFetch(() => jsonResponse({}));
    await request("/projects", { method: "POST", body: JSON.stringify({ title: "x" }) });
    expect((calls[0]?.init.headers as Record<string, string>)["Content-Type"]).toBe(
      "application/json",
    );
  });

  it("treats 204 as an empty success", async () => {
    stubFetch(() => new Response(null, { status: 204 }));
    await expect(request("/documents/abc", { method: "DELETE" })).resolves.toBeUndefined();
  });

  it("normalises the safe error envelope into an ApiError", async () => {
    stubFetch(() =>
      jsonResponse(
        {
          code: "file_rejected",
          message: "الملف تالف أو غير مدعوم.",
          request_id: "req-1",
          field_errors: { file: ["تنسيق غير مدعوم"] },
        },
        422,
      ),
    );
    const error = await request("/documents", { method: "POST" }).catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiError);
    const apiError = error as ApiError;
    expect(apiError.status).toBe(422);
    expect(apiError.code).toBe("file_rejected");
    // The server's own Arabic message is surfaced verbatim.
    expect(apiError.message).toBe("الملف تالف أو غير مدعوم.");
    expect(apiError.requestId).toBe("req-1");
    expect(apiError.fieldErrors).toEqual({ file: ["تنسيق غير مدعوم"] });
  });

  it("never surfaces raw server text when the payload is not an envelope", async () => {
    stubFetch(
      () =>
        new Response("Traceback: /srv/app/db.py line 42: SELECT * FROM users", {
          status: 500,
          headers: { "Content-Type": "text/plain" },
        }),
    );
    const error = (await request("/documents").catch((e: unknown) => e)) as ApiError;
    expect(error.status).toBe(500);
    expect(error.code).toBe("http_500");
    expect(error.message).not.toContain("Traceback");
    expect(error.message).not.toContain("SELECT");
    expect(error.message.length).toBeGreaterThan(0);
  });

  it("reports a network failure without inventing a server message", async () => {
    stubFetch(() => {
      throw new TypeError("Failed to fetch");
    });
    const error = (await request("/documents").catch((e: unknown) => e)) as ApiError;
    expect(error).toBeInstanceOf(ApiError);
    expect(error.status).toBe(0);
    expect(error.message).not.toBe("");
  });
});

describe("auth", () => {
  it("maps a 401 from /auth/me to null instead of throwing", async () => {
    stubFetch(() => jsonResponse({ code: "unauthenticated", message: "غير مصرّح" }, 401));
    await expect(auth.me()).resolves.toBeNull();
  });

  it("rethrows a non-401 failure from /auth/me", async () => {
    stubFetch(() => jsonResponse({ code: "server_error", message: "خطأ" }, 500));
    await expect(auth.me()).rejects.toBeInstanceOf(ApiError);
  });

  it("reads the CSRF token back from the session endpoint", async () => {
    stubFetch(() => jsonResponse({ csrf_token: "server-issued" }));
    await expect(auth.ensureCsrf()).resolves.toBe("server-issued");
  });
});

describe("documents", () => {
  it("encodes list pagination into the query string the API expects", async () => {
    const { calls } = stubFetch(() => jsonResponse({ items: [], total: 0, page: 2, page_size: 5 }));
    await documents.list({ page: 2, pageSize: 5 });
    expect(calls[0]?.url).toBe(`${API_PREFIX}/documents?page=2&page_size=5`);
  });

  it("omits the query string entirely for the default list call", async () => {
    const { calls } = stubFetch(() => jsonResponse({ items: [], total: 0, page: 1, page_size: 20 }));
    await documents.list();
    expect(calls[0]?.url).toBe(`${API_PREFIX}/documents`);
  });

  it("uses the API's page_size parameter name for text segments", async () => {
    const { calls } = stubFetch(() =>
      jsonResponse({ document_id: "d", status: "ready", items: [], total: 0, page: 3, page_size: 10 }),
    );
    await documents.text("doc-1", 3, 10);
    expect(calls[0]?.url).toBe(`${API_PREFIX}/documents/doc-1/text?page=3&page_size=10`);
  });

  it("sends the file and the optional project as multipart fields", async () => {
    const { calls } = stubFetch(() => jsonResponse({ id: "d", status: "pending" }));
    const file = new File(["%PDF-1.7"], "بحث.pdf", { type: "application/pdf" });
    await documents.upload(file, "proj-9");
    const body = calls[0]?.init.body as FormData;
    expect(calls[0]?.init.method).toBe("POST");
    expect((body.get("file") as File).name).toBe("بحث.pdf");
    expect(body.get("project_id")).toBe("proj-9");
  });

  it("omits project_id when no project is chosen", async () => {
    const { calls } = stubFetch(() => jsonResponse({ id: "d", status: "pending" }));
    await documents.upload(new File(["x"], "a.docx"), null);
    expect((calls[0]?.init.body as FormData).get("project_id")).toBeNull();
  });

  it("builds a relative, authorization-bearing download URL", async () => {
    const url = documents.downloadUrl("doc-1");
    expect(url).toBe(`${API_PREFIX}/documents/doc-1/download`);
    expect(url.startsWith("/")).toBe(true);
  });

  it("POSTs to the retry endpoint with a CSRF header", async () => {
    const { calls } = stubFetch(() => jsonResponse({ document_id: "d", job_id: "j", status: "pending" }));
    setCsrfCookie("tok");
    await documents.retry("doc-1");
    expect(calls[0]?.url).toBe(`${API_PREFIX}/documents/doc-1/retry`);
    expect(calls[0]?.init.method).toBe("POST");
    expect((calls[0]?.init.headers as Record<string, string>)["X-CSRF-Token"]).toBe("tok");
  });
});
