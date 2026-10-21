/**
 * Typed API contract mirror for the MAFHUM frontend.
 *
 * Single source of truth: the FastAPI/Pydantic v2 contract served by
 * `apps/api` at `GET /openapi.json` (prefix `/api/v1`). Feature code imports
 * strictly from this file rather than re-declaring payload shapes inline.
 */

export type Locale = "ar" | "fr";

export interface HealthResponse {
  status: "ok" | "degraded";
  version: string;
  checks: Record<string, { status: "ok" | "degraded" | "unavailable"; detail?: string }>;
  request_id: string;
}

export interface ErrorField {
  field: string;
  message: string;
  code?: string;
}

export interface SafeErrorEnvelope {
  code: string;
  message: string;
  request_id: string;
  field_errors?: ErrorField[];
}

export interface ApiMeta {
  version: string;
  prefix: string;
}

export interface ProviderHealth {
  provider: string;
  model?: string;
  status: "ok" | "degraded" | "unavailable";
  deterministic: boolean;
  credentials_required: boolean;
}

export interface GenerateRequest {
  prompt: string;
  max_tokens?: number;
  temperature?: number;
}

export interface GenerateResponse {
  text: string;
  provider: string;
  model: string;
  draft: boolean;
  usage?: { prompt_tokens?: number; completion_tokens?: number };
  request_id: string;
}

/* --- Phase 2: Identity, Sessions, Profile, Quotas, Projects --- */

export interface UserRegisterRequest {
  email: string;
  password: string;
  full_name?: string | null;
  preferred_locale?: Locale;
}

export interface UserLoginRequest {
  email: string;
  password: string;
}

export interface UserOut {
  id: string;
  email: string;
  full_name: string | null;
  preferred_locale: Locale;
  is_active: boolean;
  project_quota: number;
  projects_count: number;
  created_at: string;
  updated_at: string;
}

export interface AuthMessage {
  detail: string;
}

export interface SessionOut {
  id: string;
  created_at: string;
  expires_at: string;
  last_used_at: string | null;
  is_current: boolean;
}

export interface SessionListOut {
  items: SessionOut[];
}

export interface ProfileUpdateRequest {
  full_name?: string | null;
  preferred_locale?: Locale;
}

export interface UsageOut {
  project_quota: number;
  projects_used: number;
  projects_remaining: number;
}

export interface ProjectCreateRequest {
  title: string;
  description?: string | null;
}

export interface ProjectUpdateRequest {
  title?: string;
  description?: string | null;
}

export interface ProjectOut {
  id: string;
  owner_id: string;
  title: string;
  description: string | null;
  created_at: string;
  updated_at: string;
}

export interface ProjectList {
  items: ProjectOut[];
  limit: number;
  offset: number;
  total: number;
}

export interface AccountExportOut {
  exported_at: string;
  user: UserOut;
  projects: ProjectOut[];
  retention_notice: string;
}

export interface DeleteAccountResponse {
  detail: string;
  deleted_user_id: string;
  sessions_revoked: number;
  projects_deleted: number;
  retention_notice: string;
}
