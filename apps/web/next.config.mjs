/**
 * MAFHUM web configuration.
 *
 * The browser never talks to the API host directly: `/api` is proxied by the
 * Next.js server to the FastAPI backend. That keeps cookies same-origin (so
 * SameSite and the CSRF double-submit work) and means no backend origin has to be
 * exposed to the browser.
 */
const API_TARGET = process.env.MAFHUM_API_URL ?? "http://127.0.0.1:8000";

/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  poweredByHeader: false,
  /**
   * Next's dev server refuses cross-origin requests unless the host is allowlisted.
   * Read from the environment so no deployment-specific hostname is committed.
   * Only affects `next dev`; production builds ignore it.
   */
  allowedDevOrigins: (process.env.MAFHUM_ALLOWED_DEV_ORIGINS ?? "")
    .split(",")
    .map((entry) => entry.trim())
    .filter(Boolean),
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: `${API_TARGET}/api/:path*`,
      },
      {
        source: "/health/:path*",
        destination: `${API_TARGET}/health/:path*`,
      },
    ];
  },
  async headers() {
    return [
      {
        source: "/:path*",
        headers: [
          { key: "X-Frame-Options", value: "DENY" },
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "Referrer-Policy", value: "no-referrer" },
        ],
      },
    ];
  },
};

export default nextConfig;
