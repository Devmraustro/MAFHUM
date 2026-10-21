import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import AccountPage from "./page";
import { LocalesProvider } from "@/components/locale-provider";
import * as apiMod from "@/lib/api";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn() }),
}));

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("AccountPage", () => {
  it("renders active sessions, retention disclosure, and enforces explicit confirmation before account deletion", async () => {
    vi.spyOn(apiMod.api, "getMe").mockResolvedValue({
      id: "u1",
      email: "nadia@usthb.dz",
      full_name: "نادية",
      preferred_locale: "ar",
      is_active: true,
      project_quota: 5,
      projects_count: 2,
      created_at: "2026-10-21T10:00:00Z",
      updated_at: "2026-10-21T10:00:00Z",
    });
    vi.spyOn(apiMod.api, "listSessions").mockResolvedValue({
      items: [
        {
          id: "sess-current-1234",
          created_at: "2026-10-21T10:00:00Z",
          expires_at: "2026-10-28T10:00:00Z",
          last_used_at: "2026-10-21T10:05:00Z",
          is_current: true,
        },
        {
          id: "sess-other-5678",
          created_at: "2026-10-20T10:00:00Z",
          expires_at: "2026-10-27T10:00:00Z",
          last_used_at: null,
          is_current: false,
        },
      ],
    });
    const deleteSpy = vi.spyOn(apiMod.api, "deleteAccount").mockResolvedValue({
      detail: "Account deleted",
      deleted_user_id: "u1",
      sessions_revoked: 2,
      projects_deleted: 2,
      retention_notice: "Backups follow retention window.",
    });

    localStorage.setItem("mafhum-locale", "ar");
    render(
      <LocalesProvider>
        <AccountPage />
      </LocalesProvider>,
    );

    expect(await screen.findByText("الجلسة الحالية")).toBeInTheDocument();
    expect(screen.getByText(/النسخ الاحتياطية المشفرة/)).toBeInTheDocument();

    const deleteButton = screen.getByRole("button", { name: "حذف الحساب نهائيًا" });
    expect(deleteButton).toBeDisabled();

    fireEvent.click(screen.getByRole("checkbox"));
    expect(deleteButton).not.toBeDisabled();

    fireEvent.click(deleteButton);
    expect(deleteSpy).toHaveBeenCalledTimes(1);
  });
});
