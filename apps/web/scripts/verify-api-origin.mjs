/**
 * Refuse to build a web bundle whose API origin cannot work.
 *
 * Why this exists: the Vercel project for this repository was configured with
 * `MAFHUM_API_URL=https://mafhum-api.invalid` — a deliberately unroutable
 * placeholder. Presence-only validation accepted it, the build succeeded, and the
 * deployed site would have answered every registration and login with a network
 * error while looking perfectly healthy. A build that ships a dead backend origin
 * is worse than a build that fails.
 *
 * So the origin is validated, not merely required:
 *  - it must parse as an absolute http(s) URL;
 *  - it must not use a reserved, non-resolving TLD (`.invalid`, `.example`,
 *    `.test`, `.localhost`), which can only ever be a placeholder;
 *  - it must not be a bare loopback address, because a browser-facing site whose
 *    API is `127.0.0.1` talks to the visitor's own machine.
 *
 * Container builds are unaffected: they point at the compose service name
 * (`http://api:8000`), which is neither reserved nor loopback.
 */

/** TLDs reserved by RFC 2606/6761 — resolvable only in a lab, never in production. */
const RESERVED_SUFFIXES = [".invalid", ".example", ".test", ".localhost", ".local"];

/** Loopback hosts: valid for local development, never for a deployed site. */
const LOOPBACK_HOSTS = new Set(["localhost", "127.0.0.1", "0.0.0.0", "[::1]", "::1"]);

/**
 * @param {string | undefined} raw
 * @param {{deployed?: boolean}} [context] `deployed` means the artifact will be
 *   served from the public internet, where a loopback API origin is meaningless.
 *   Local builds and CI are not deployed, so they may legitimately use loopback.
 * @returns {{ok: true} | {ok: false, reason: string}}
 */
export function validateApiOrigin(raw, context = {}) {
  if (typeof raw !== "string" || raw.trim() === "") {
    return { ok: false, reason: "MAFHUM_API_URL is not set" };
  }
  let url;
  try {
    url = new URL(raw.trim());
  } catch {
    return { ok: false, reason: `MAFHUM_API_URL is not an absolute URL: ${raw}` };
  }
  if (url.protocol !== "http:" && url.protocol !== "https:") {
    return { ok: false, reason: `MAFHUM_API_URL must be http(s), got ${url.protocol}` };
  }
  const host = url.hostname.toLowerCase();
  // Reserved TLDs are rejected everywhere: they can never resolve, deployed or not.
  if (RESERVED_SUFFIXES.some((suffix) => host.endsWith(suffix))) {
    return {
      ok: false,
      reason:
        `MAFHUM_API_URL uses a reserved, non-resolving domain (${host}). ` +
        "This is a placeholder: set the real API origin before deploying.",
    };
  }
  if (context.deployed && LOOPBACK_HOSTS.has(host)) {
    return {
      ok: false,
      reason:
        `MAFHUM_API_URL points at a loopback host (${host}); a deployed site would ` +
        "call the visitor's own machine. Use the public API origin.",
    };
  }
  return { ok: true };
}

// Executed directly by the build command; imported by the unit test.
if (process.argv[1] && import.meta.url === `file://${process.argv[1]}`) {
  // `VERCEL=1` is set by the Vercel build environment. A Next.js build there is
  // by definition going to be served publicly.
  const deployed = process.env.VERCEL === "1";
  const result = validateApiOrigin(process.env.MAFHUM_API_URL, { deployed });
  if (!result.ok) {
    console.error(`✗ ${result.reason}`);
    process.exit(1);
  }
  console.log(`✓ API origin accepted: ${process.env.MAFHUM_API_URL}`);
}
