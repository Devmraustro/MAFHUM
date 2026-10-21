/**
 * Browser API client using same-origin `/api/v1` proxy routes, HttpOnly session
 * cookies, double-submit CSRF headers, and typed SafeErrorEnvelope handling.
 */

import type {
  AccountExportOut,
  AuthMessage,
  DeleteAccountResponse,
  ErrorField,
  ProfileUpdateRequest,
  ProjectCreateRequest,
  ProjectList,
  ProjectOut,
  ProjectUpdateRequest,
  SafeErrorEnvelope,
  SessionListOut,
  UsageOut,
  UserLoginRequest,
  UserOut,
  UserRegisterRequest,
} from "./contract";

export class ApiClientError extends Error {
  readonly status: number;
  readonly code: string;
  readonly requestId: string;
  readonly fieldErrors: ErrorField[];

  constructor(status: number, envelope: SafeErrorEnvelope) {
    super(envelope.message);
    this.name = "ApiClientError";
    this.status = status;
    this.code = envelope.code;
    this.requestId = envelope.request_id;
    this.fieldErrors = envelope.field_errors ?? [];
  }
}

export function readCookie(name: string): string | null {
  if (typeof document === "undefined") return null;
  const parts = document.cookie ? document.cookie.split("; ") : [];
  for (const part of parts) {
    const eqIdx = part.indexOf("=");
    if (eqIdx === -1) continue;
    const key = decodeURIComponent(part.slice(0, eqIdx));
    if (key === name) {
      return decodeURIComponent(part.slice(eqIdx + 1));
    }
  }
  return null;
}

async function ensureCsrfToken(): Promise<string | null> {
  const existing = readCookie("mafhum_csrf");
  if (existing) return existing;
  await fetch("/api/v1/auth/csrf-token", {
    method: "GET",
    credentials: "include",
  });
  return readCookie("mafhum_csrf");
}

async function request<T>(
  path: string,
  options: {
    method?: "GET" | "POST" | "PATCH" | "DELETE";
    body?: unknown;
    csrf?: boolean;
  } = {},
): Promise<T> {
  const method = options.method ?? "GET";
  const headers: Record<string, string> = {};

  if (options.body !== undefined) {
    headers["Content-Type"] = "application/json";
  }

  if (options.csrf) {
    const csrfToken = await ensureCsrfToken();
    if (csrfToken) {
      headers["X-CSRF-Token"] = csrfToken;
    }
  }

  const response = await fetch(`/api/v1${path}`, {
    method,
    credentials: "include",
    headers,
    body: options.body !== undefined ? JSON.stringify(options.body) : undefined,
  });

  if (!response.ok) {
    let envelope: SafeErrorEnvelope = {
      code: `http_${response.status}`,
      message: "Request failed",
      request_id: response.headers.get("x-request-id") ?? "",
    };
    try {
      const parsed = (await response.json()) as Partial<SafeErrorEnvelope>;
      if (parsed && typeof parsed.message === "string" && typeof parsed.code === "string") {
        envelope = {
          code: parsed.code,
          message: parsed.message,
          request_id: parsed.request_id ?? envelope.request_id,
          field_errors: parsed.field_errors,
        };
      }
    } catch {
      // Fall back to default safe envelope when non-JSON response received.
    }
    throw new ApiClientError(response.status, envelope);
  }

  if (response.status === 204) {
    return undefined as T;
  }
  return (await response.json()) as T;
}

export const api = {
  register: (payload: UserRegisterRequest) =>
    request<UserOut>("/auth/register", { method: "POST", body: payload }),

  login: (payload: UserLoginRequest) =>
    request<UserOut>("/auth/login", { method: "POST", body: payload }),

  logout: () =>
    request<AuthMessage>("/auth/logout", { method: "POST", csrf: true }),

  logoutAll: () =>
    request<AuthMessage>("/auth/logout-all", { method: "POST", csrf: true }),

  listSessions: () => request<SessionListOut>("/auth/sessions"),

  revokeSession: (sessionId: string) =>
    request<AuthMessage>(`/auth/sessions/${encodeURIComponent(sessionId)}`, {
      method: "DELETE",
      csrf: true,
    }),

  getMe: () => request<UserOut>("/me"),

  updateProfile: (payload: ProfileUpdateRequest) =>
    request<UserOut>("/me", { method: "PATCH", body: payload, csrf: true }),

  getUsage: () => request<UsageOut>("/me/usage"),

  exportAccount: () => request<AccountExportOut>("/me/export"),

  deleteAccount: () =>
    request<DeleteAccountResponse>("/me", { method: "DELETE", csrf: true }),

  listProjects: (limit = 20, offset = 0) =>
    request<ProjectList>(`/projects?limit=${limit}&offset=${offset}`),

  getProject: (id: string) =>
    request<ProjectOut>(`/projects/${encodeURIComponent(id)}`),

  createProject: (payload: ProjectCreateRequest) =>
    request<ProjectOut>("/projects", { method: "POST", body: payload, csrf: true }),

  updateProject: (id: string, payload: ProjectUpdateRequest) =>
    request<ProjectOut>(`/projects/${encodeURIComponent(id)}`, {
      method: "PATCH",
      body: payload,
      csrf: true,
    }),

  deleteProject: (id: string) =>
    request<void>(`/projects/${encodeURIComponent(id)}`, {
      method: "DELETE",
      csrf: true,
    }),
};
