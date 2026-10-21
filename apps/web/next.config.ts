import type { NextConfig } from "next";

const apiUpstream =
  process.env.API_UPSTREAM_URL ||
  process.env.NEXT_PUBLIC_API_URL ||
  "http://127.0.0.1:8000";

const nextConfig: NextConfig = {
  cacheComponents: true,
  partialPrefetching: true,
  turbopack: {
    rules: {
      "*.css": {
        loaders: ["@tailwindcss/turbopack"],
        as: "*.css",
      },
    },
  },
  async rewrites() {
    return [
      {
        source: "/api/v1/:path*",
        destination: `${apiUpstream}/api/v1/:path*`,
      },
      {
        source: "/health/:path*",
        destination: `${apiUpstream}/health/:path*`,
      },
    ];
  },
};

export default nextConfig;
