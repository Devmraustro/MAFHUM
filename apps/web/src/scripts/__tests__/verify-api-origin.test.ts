import { describe, expect, it } from "vitest";

import { validateApiOrigin } from "../../../scripts/verify-api-origin.mjs";

/**
 * The build-time guard that stops a deployment from shipping a placeholder API
 * origin. This was not hypothetical: the Vercel project was found configured with
 * `https://mafhum-api.invalid`, and the previous build command only checked that
 * the variable was *set*, so the broken bundle was published successfully.
 */
describe("API origin validation", () => {
  it("rejects the placeholder that was actually deployed", () => {
    const result = validateApiOrigin("https://mafhum-api.invalid", { deployed: true });
    expect(result.ok).toBe(false);
    expect(result.reason).toContain("reserved");
  });

  it("rejects every reserved TLD, deployed or not", () => {
    for (const host of [
      "https://api.invalid",
      "https://api.example",
      "https://api.test",
      "https://api.localhost",
    ]) {
      expect(validateApiOrigin(host).ok, host).toBe(false);
    }
  });

  it("rejects a loopback origin on a deployment", () => {
    const result = validateApiOrigin("http://127.0.0.1:8000", { deployed: true });
    expect(result.ok).toBe(false);
    expect(result.reason).toContain("loopback");
  });

  it("accepts a loopback origin for a local build", () => {
    // `next build` on a workstation talks to the local API; that is legitimate.
    expect(validateApiOrigin("http://127.0.0.1:8000").ok).toBe(true);
    expect(validateApiOrigin("http://localhost:8000").ok).toBe(true);
  });

  it("accepts the compose service name used by the container stack", () => {
    expect(validateApiOrigin("http://api:8000", { deployed: true }).ok).toBe(true);
  });

  it("accepts a real https origin", () => {
    expect(validateApiOrigin("https://mafhum-api.example.org", { deployed: true }).ok).toBe(true);
  });

  it("rejects missing, relative and non-http values", () => {
    expect(validateApiOrigin(undefined).ok).toBe(false);
    expect(validateApiOrigin("   ").ok).toBe(false);
    expect(validateApiOrigin("/api").ok).toBe(false);
    expect(validateApiOrigin("ftp://api.example.org").ok).toBe(false);
  });
});
