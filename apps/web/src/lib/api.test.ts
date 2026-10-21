import { afterEach, describe, expect, it, vi } from "vitest";
import { api, ApiClientError, readCookie } from "./api";

afterEach(() => {
  vi.restoreAllMocks();
  document.cookie = "mafhum_csrf=; Max-Age=0; path=/";
});

describe("API client security & error handling", () => {
  it("reads cookies safely and attaches X-CSRF-Token on state-changing requests", async () => {
    document.cookie = "mafhum_csrf=test-csrf-token-123; path=/";
    expect(readCookie("mafhum_csrf")).toBe("test-csrf-token-123");

    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          id: "11111111-1111-1111-1111-111111111111",
          owner_id: "22222222-2222-2222-2222-222222222222",
          title: "مشروع تجريبي",
          description: null,
          created_at: "2026-10-21T10:00:00Z",
          updated_at: "2026-10-21T10:00:00Z",
        }),
        { status: 201 },
      ),
    );

    const created = await api.createProject({ title: "مشروع تجريبي" });
    expect(created.title).toBe("مشروع تجريبي");
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/v1/projects");
    expect(init?.credentials).toBe("include");
    expect((init?.headers as Record<string, string>)["X-CSRF-Token"]).toBe(
      "test-csrf-token-123",
    );
  });

  it("maps SafeErrorEnvelope and request_id onto ApiClientError", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          code: "http_401",
          message: "Authentication required",
          request_id: "req-xyz-999",
        }),
        { status: 401 },
      ),
    );

    await expect(api.getMe()).rejects.toMatchObject({
      status: 401,
      code: "http_401",
      requestId: "req-xyz-999",
      message: "Authentication required",
    } satisfies Partial<ApiClientError>);
  });
});
