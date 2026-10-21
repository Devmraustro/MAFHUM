/**
 * Typed API contract mirror for the MAFHUM frontend.
 *
 * Single source of truth: the FastAPI/Pydantic v2 contract served by
 * `apps/api` at `GET /api/v1/openapi.json` (root `GET /health/live` and
 * `GET /health/ready` as well). These TypeScript declarations mirror that
 * contract by hand and MUST be kept in sync when the API changes. They are
 * intentionally the only duplicated schema layer — feature code imports from
 * this file rather than re-declaring shapes inline.
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
