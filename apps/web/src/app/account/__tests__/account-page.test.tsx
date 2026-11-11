import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import AccountPage from "@/app/account/page";
import { LocaleProvider } from "@/lib/locale-context";
import { t } from "@/lib/i18n";

/**
 * Account page behaviour against a stubbed network.
 *
 * The states that matter and are easy to get wrong:
 * - an unverified address on a deployment that *can* send mail shows the button;
 * - an unverified address on a deployment that cannot send mail shows the reason
 *   instead of a button that would always fail;
 * - the current device is labelled, and another device can be signed out;
 * - a wrong current password surfaces as a field error, not a generic banner.
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
      const handler = routes[`${method} ${path.split("?")[0]}`];
      if (!handler) {
        return jsonResponse(404, { code: "not_found", message: `no route: ${method} ${path}` });
      }
      return handler(call);
    }),
  );
  return calls;
}

function renderPage() {
  return render(
    <LocaleProvider>
      <AccountPage />
    </LocaleProvider>,
  );
}

const BASE = {
  "GET /auth/session": () => jsonResponse(200, { csrf_token: "csrf-1" }),
  "GET /auth/me": () => jsonResponse(200, USER),
};

describe("account page", () => {
  beforeEach(() => {
    routerStub.push.mockClear();
    routerStub.replace.mockClear();
    document.cookie = "mafhum_csrf=csrf-1";
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("offers verification when delivery is configured", async () => {
    routeFetch({
      ...BASE,
      "GET /auth/account-state": () =>
        jsonResponse(200, {
          email: USER.email,
          email_verified: false,
          email_delivery_configured: true,
          password_reset_available: true,
        }),
      "GET /auth/sessions": () => jsonResponse(200, { items: [], total: 0 }),
    });
    renderPage();

    const button = await screen.findByRole("button", { name: t("ar", "account.verifyNow") });
    expect(button).toBeTruthy();
  });

  it("explains instead of offering a button that cannot work", async () => {
    routeFetch({
      ...BASE,
      "GET /auth/account-state": () =>
        jsonResponse(200, {
          email: USER.email,
          email_verified: false,
          email_delivery_configured: false,
          password_reset_available: false,
        }),
      "GET /auth/sessions": () => jsonResponse(200, { items: [], total: 0 }),
    });
    renderPage();

    expect(await screen.findByText(t("ar", "account.verifyUnavailable"))).toBeTruthy();
    expect(screen.queryByRole("button", { name: t("ar", "account.verifyNow") })).toBeNull();
  });

  it("labels the current session and revokes another one", async () => {
    let revoked = false;
    routeFetch({
      ...BASE,
      "GET /auth/account-state": () =>
        jsonResponse(200, {
          email: USER.email,
          email_verified: true,
          email_delivery_configured: true,
          password_reset_available: true,
        }),
      "GET /auth/sessions": () =>
        jsonResponse(200, {
          total: 2,
          items: [
            {
              id: "s-1",
              created_at: "2026-11-01T10:00:00Z",
              expires_at: "2026-11-08T10:00:00Z",
              user_agent: "Firefox on Linux",
              current: true,
            },
            {
              id: "s-2",
              created_at: "2026-10-30T10:00:00Z",
              expires_at: "2026-11-06T10:00:00Z",
              user_agent: "Chrome on Android",
              current: false,
            },
          ],
        }),
      "DELETE /auth/sessions/s-2": () => {
        revoked = true;
        return { ok: true, status: 204, text: async () => "" } as unknown as Response;
      },
    });
    renderPage();

    expect(await screen.findByText(t("ar", "account.sessionCurrent"))).toBeTruthy();
    expect(screen.getByText("Chrome on Android")).toBeTruthy();

    const revokeButtons = screen.getAllByRole("button", {
      name: t("ar", "account.sessionRevoke"),
    });
    fireEvent.click(revokeButtons[1] as HTMLElement);
    // Destructive actions go through the confirm dialog first.
    const confirm = await screen.findByRole("dialog");
    fireEvent.click(
      Array.from(confirm.querySelectorAll("button")).find(
        (button) => button.textContent === t("ar", "account.sessionRevoke"),
      ) as HTMLElement,
    );
    await waitFor(() => expect(revoked).toBe(true));
  });

  it("shows a field error when the current password is wrong", async () => {
    routeFetch({
      ...BASE,
      "GET /auth/account-state": () =>
        jsonResponse(200, {
          email: USER.email,
          email_verified: true,
          email_delivery_configured: true,
          password_reset_available: true,
        }),
      "GET /auth/sessions": () => jsonResponse(200, { items: [], total: 0 }),
      "POST /auth/password/change": () =>
        jsonResponse(422, {
          code: "validation_error",
          message: "كلمة المرور الحالية غير صحيحة.",
          field_errors: { current_password: ["غير صحيحة."] },
        }),
    });
    renderPage();

    await screen.findByRole("heading", { name: t("ar", "account.changePassword") });
    fireEvent.change(screen.getByLabelText(t("ar", "account.currentPassword")), {
      target: { value: "wrong-one" },
    });
    fireEvent.change(screen.getByLabelText(t("ar", "auth.newPassword")), {
      target: { value: "a-new-password" },
    });
    fireEvent.click(screen.getByRole("button", { name: t("ar", "account.changePasswordSubmit") }));

    expect(await screen.findByText("غير صحيحة.")).toBeTruthy();
  });

  it("sends an expired session back to the sign-in page", async () => {
    routeFetch({
      "GET /auth/session": () =>
        jsonResponse(401, { code: "unauthenticated", message: "انتهت الجلسة." }),
      "GET /auth/me": () => jsonResponse(401, { code: "unauthenticated", message: "انتهت" }),
    });
    renderPage();
    await waitFor(() => expect(routerStub.replace).toHaveBeenCalledWith("/login"));
  });
});
