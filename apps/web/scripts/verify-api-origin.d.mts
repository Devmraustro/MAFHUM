/**
 * Types for the build-time guard. Declaring them here (rather than converting the
 * script to TypeScript) keeps it directly executable by the Vercel build command
 * with plain `node`, which has no build step of its own.
 */
export interface ApiOriginVerdict {
  ok: boolean;
  reason?: string;
}

export function validateApiOrigin(
  raw: string | undefined,
  context?: { deployed?: boolean },
): ApiOriginVerdict;
