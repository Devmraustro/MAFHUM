import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import WorkspacePage from "./page";
import { LocalesProvider } from "@/components/locale-provider";
import { ApiClientError } from "@/lib/api";
import * as apiMod from "@/lib/api";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("WorkspacePage", () => {
  it("shows authentication prompt when unauthenticated (401)", async () => {
    vi.spyOn(apiMod.api, "getMe").mockRejectedValueOnce(
      new ApiClientError(401, {
        code: "http_401",
        message: "Authentication required",
        request_id: "req-unauth",
      }),
    );
    vi.spyOn(apiMod.api, "getUsage").mockRejectedValueOnce(new Error("unauth"));
    vi.spyOn(apiMod.api, "listProjects").mockRejectedValueOnce(new Error("unauth"));

    localStorage.setItem("mafhum-locale", "ar");
    render(
      <LocalesProvider>
        <WorkspacePage />
      </LocalesProvider>,
    );

    expect(await screen.findByText("يتطلب الوصول تسجيل الدخول")).toBeInTheDocument();
  });

  it("renders quota meter, lists owned projects, and creates a new project", async () => {
    vi.spyOn(apiMod.api, "getMe").mockResolvedValue({
      id: "u1",
      email: "student@univ-setif.dz",
      full_name: "أمينة",
      preferred_locale: "ar",
      is_active: true,
      project_quota: 5,
      projects_count: 1,
      created_at: "2026-10-21T10:00:00Z",
      updated_at: "2026-10-21T10:00:00Z",
    });
    vi.spyOn(apiMod.api, "getUsage").mockResolvedValue({
      project_quota: 5,
      projects_used: 1,
      projects_remaining: 4,
    });
    vi.spyOn(apiMod.api, "listProjects").mockResolvedValue({
      items: [
        {
          id: "p1",
          owner_id: "u1",
          title: "القانون الدستوري الجزائري",
          description: "ملخص المحاضرات",
          created_at: "2026-10-21T10:00:00Z",
          updated_at: "2026-10-21T10:00:00Z",
        },
      ],
      limit: 10,
      offset: 0,
      total: 1,
    });
    const createSpy = vi.spyOn(apiMod.api, "createProject").mockResolvedValue({
      id: "p2",
      owner_id: "u1",
      title: "منهجية البحث العلمي",
      description: null,
      created_at: "2026-10-21T11:00:00Z",
      updated_at: "2026-10-21T11:00:00Z",
    });

    localStorage.setItem("mafhum-locale", "ar");
    render(
      <LocalesProvider>
        <WorkspacePage />
      </LocalesProvider>,
    );

    expect(await screen.findByText("القانون الدستوري الجزائري")).toBeInTheDocument();
    expect(screen.getByText("1 / 5")).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("عنوان المشروع"), {
      target: { value: "منهجية البحث العلمي" },
    });
    fireEvent.click(screen.getByRole("button", { name: "إنشاء المشروع" }));

    await waitFor(() => {
      expect(createSpy).toHaveBeenCalledWith({
        title: "منهجية البحث العلمي",
        description: null,
      });
    });
  });
});
