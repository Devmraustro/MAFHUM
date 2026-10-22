/**
 * Typed client for the MAFHUM API.
 *
 * Conventions
 * -----------
 * - All URLs are **relative** (`/api/v1/...`). The Next.js server proxies them to
 *   the API, so the browser never needs to know the backend origin and cookies
 *   stay same-origin.
 * - Cookies carry the session, so every request uses `credentials: "include"`.
 * - Mutations must echo the CSRF cookie in `X-CSRF-Token`; the API rejects them
 *   otherwise.
 * - Errors are normalised into {@link ApiError} using the server's safe envelope.
 *   Raw response text is never surfaced, so an unexpected server payload cannot
 *   leak internals into the UI.
 */

export const API_PREFIX = "/api/v1";

export type DocumentStatus = "pending" | "processing" | "ready" | "failed";
export type DocumentFormat = "pdf" | "docx";

export interface ApiErrorBody {
  code: string;
  message: string;
  request_id?: string | null;
  field_errors?: Record<string, string[]> | null;
  details?: Record<string, unknown> | null;
}

export class ApiError extends Error {
  readonly code: string;
  readonly status: number;
  readonly requestId: string | null;
  readonly fieldErrors: Record<string, string[]>;
  readonly details: Record<string, unknown>;

  constructor(status: number, body: ApiErrorBody | null, fallbackMessage: string) {
    super(body?.message ?? fallbackMessage);
    this.name = "ApiError";
    this.status = status;
    this.code = body?.code ?? `http_${status}`;
    this.requestId = body?.request_id ?? null;
    this.fieldErrors = body?.field_errors ?? {};
    this.details = body?.details ?? {};
  }
}

export interface User {
  id: string;
  email: string;
  display_name: string;
  role: string;
  preferred_language: string;
}

export interface Project {
  id: string;
  title: string;
  description: string;
  language: string;
  created_at: string;
  updated_at: string;
}

export interface DocumentSummary {
  id: string;
  display_filename: string;
  format: DocumentFormat;
  size_bytes: number;
  status: DocumentStatus;
  page_count: number | null;
  char_count: number;
  segment_count: number;
  project_id: string | null;
  language_hint: string | null;
  is_scanned: boolean | null;
  parser_name: string | null;
  parser_version: string | null;
  error_code: string | null;
  error_message: string | null;
  created_at: string;
  processed_at: string | null;
}

export interface DocumentListResponse {
  items: DocumentSummary[];
  total: number;
  page: number;
  page_size: number;
}

export interface TextSegment {
  ordinal: number;
  page_number: number | null;
  text: string;
  char_count: number;
}

export interface DocumentTextResponse {
  document_id: string;
  status: DocumentStatus;
  items: TextSegment[];
  total: number;
  page: number;
  page_size: number;
}

export interface Usage {
  documents: number;
  total_bytes: number;
  max_documents: number;
  max_total_bytes: number;
}

export interface UploadLimits {
  max_upload_mb: number;
  max_pdf_pages: number;
  max_documents_per_user: number;
  max_total_bytes_per_user_mb: number;
  allowed_extensions: string[];
  ocr_available: boolean;
  /** False while MAFHUM ships no antivirus engine; uploads are not virus-checked. */
  malware_scanning_available: boolean;
}

const CSRF_COOKIE = "mafhum_csrf";

/** Read the CSRF token from the double-submit cookie, if present. */
export function readCsrfToken(): string | null {
  if (typeof document === "undefined") return null;
  const match = document.cookie.match(new RegExp(`(?:^|;\\s*)${CSRF_COOKIE}=([^;]*)`));
  return match?.[1] ? decodeURIComponent(match[1]) : null;
}

function isMutation(method: string): boolean {
  return method !== "GET" && method !== "HEAD";
}

interface RequestOptions {
  method?: string;
  body?: BodyInit | null;
  headers?: Record<string, string>;
  signal?: AbortSignal;
}

/**
 * Perform a request and decode the JSON envelope.
 *
 * The CSRF token is resolved at call time rather than module load so a token
 * issued after page load is picked up.
 */
export async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const method = options.method ?? "GET";
  const headers: Record<string, string> = { ...(options.headers ?? {}) };

  if (isMutation(method)) {
    const token = readCsrfToken();
    if (token) headers["X-CSRF-Token"] = token;
  }
  if (options.body && !(options.body instanceof FormData) && !headers["Content-Type"]) {
    headers["Content-Type"] = "application/json";
  }

  let response: Response;
  try {
    response = await fetch(`${API_PREFIX}${path}`, {
      method,
      headers,
      body: options.body ?? undefined,
      credentials: "include",
      cache: "no-store",
      signal: options.signal,
    });
  } catch {
    // Network failure or an aborted request. No server payload to interpret.
    throw new ApiError(0, null, "تعذّر الاتصال بالخادم. تحقق من الاتصال ثم حاول مجددًا.");
  }

  if (response.status === 204) {
    return undefined as T;
  }

  const text = await response.text();
  let parsed: unknown = null;
  if (text) {
    try {
      parsed = JSON.parse(text);
    } catch {
      parsed = null;
    }
  }

  if (!response.ok) {
    const body = isApiErrorBody(parsed) ? parsed : null;
    throw new ApiError(response.status, body, "حدث خطأ غير متوقع. حاول مجددًا.");
  }
  return parsed as T;
}

function isApiErrorBody(value: unknown): value is ApiErrorBody {
  return (
    typeof value === "object" &&
    value !== null &&
    "code" in value &&
    typeof (value as ApiErrorBody).code === "string"
  );
}

// --------------------------------------------------------------------- auth
export const auth = {
  async login(email: string, password: string): Promise<User> {
    const session = await request<{ user: User }>("/auth/login", {
      method: "POST",
      body: JSON.stringify({ email, password }),
    });
    return session.user;
  },

  async register(email: string, password: string, displayName: string): Promise<User> {
    const session = await request<{ user: User }>("/auth/register", {
      method: "POST",
      body: JSON.stringify({ email, password, display_name: displayName }),
    });
    return session.user;
  },

  async logout(): Promise<void> {
    await request<void>("/auth/logout", { method: "POST" });
  },

  /** Returns the current user, or null when unauthenticated. */
  async me(): Promise<User | null> {
    try {
      return await request<User>("/auth/me");
    } catch (error) {
      if (error instanceof ApiError && error.status === 401) return null;
      throw error;
    }
  },

  /** Ensure a readable CSRF cookie exists for the current session. */
  async ensureCsrf(): Promise<string | null> {
    try {
      const body = await request<{ csrf_token: string }>("/auth/session");
      return body.csrf_token;
    } catch {
      return readCsrfToken();
    }
  },
};

// ----------------------------------------------------------------- projects
export const projects = {
  list(): Promise<{ items: Project[]; total: number }> {
    return request("/projects");
  },
  create(title: string, language: string): Promise<Project> {
    return request("/projects", {
      method: "POST",
      body: JSON.stringify({ title, language }),
    });
  },
  remove(id: string): Promise<void> {
    return request(`/projects/${id}`, { method: "DELETE" });
  },
};

// ----------------------------------------------------------------- research
export interface ResearchCitation {
  kind: string;
  quote: string;
  document_id: string | null;
  source_id: string | null;
  title: string | null;
  page_number: number | null;
  ordinal: number | null;
  url: string | null;
  locator: string;
  score: number;
}

export type ResearchStatus = "grounded" | "insufficient_evidence" | "no_evidence_available";

export interface ResearchAnswer {
  question: string;
  language: string;
  status: ResearchStatus;
  evidence_sufficient: boolean;
  coverage: number;
  explanation: string;
  citations: ResearchCitation[];
}

export interface KnownSource {
  key: string;
  title: string;
  institution: string | null;
  url: string | null;
  language: string | null;
  notes: string | null;
}

export interface KnownSourceList {
  items: KnownSource[];
  /** False means no external source has actually been fetched. */
  retrieval_enabled: boolean;
}

// ---------------------------------------------------------------- documents
export const documents = {
  list(params: { page?: number; pageSize?: number; projectId?: string } = {}): Promise<DocumentListResponse> {
    const query = new URLSearchParams();
    if (params.page) query.set("page", String(params.page));
    if (params.pageSize) query.set("page_size", String(params.pageSize));
    if (params.projectId) query.set("project_id", params.projectId);
    const suffix = query.toString();
    return request(`/documents${suffix ? `?${suffix}` : ""}`);
  },

  get(id: string): Promise<DocumentSummary> {
    return request(`/documents/${id}`);
  },

  text(id: string, page = 1, pageSize = 20): Promise<DocumentTextResponse> {
    return request(`/documents/${id}/text?page=${page}&page_size=${pageSize}`);
  },

  /**
   * Upload a file. The browser sets the multipart boundary, so no Content-Type
   * header is added here.
   */
  upload(file: File, projectId?: string | null): Promise<DocumentSummary> {
    const form = new FormData();
    form.append("file", file, file.name);
    if (projectId) form.append("project_id", projectId);
    return request("/documents", { method: "POST", body: form });
  },

  retry(id: string): Promise<{ document_id: string; job_id: string; status: string }> {
    return request(`/documents/${id}/retry`, { method: "POST" });
  },

  remove(id: string): Promise<void> {
    return request(`/documents/${id}`, { method: "DELETE" });
  },

  usage(): Promise<Usage> {
    return request("/documents/usage");
  },

  limits(): Promise<UploadLimits> {
    return request("/documents/limits");
  },

  /** Relative download URL; the API authorizes and streams the bytes. */
  downloadUrl(id: string): string {
    return `${API_PREFIX}/documents/${id}/download`;
  },
};

export const research = {
  ask(question: string): Promise<ResearchAnswer> {
    return request("/research/ask", { method: "POST", body: JSON.stringify({ question }) });
  },
  sources(): Promise<KnownSourceList> {
    return request("/research/sources");
  },
};
