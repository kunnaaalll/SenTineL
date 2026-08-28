/**
 * Sentinel frontend — Next.js configuration.
 *
 * Backend connectivity model (no secrets involved anywhere):
 *
 *   Browser ──relative /backend/*──▶ Next.js server ──BACKEND_ORIGIN──▶ FastAPI
 *
 * - `lib/api.ts` defaults to the relative base "/backend". The rewrite below
 *   proxies those calls to the backend origin read from BACKEND_ORIGIN when
 *   the server starts, so the target is RUNTIME-configurable (compose sets
 *   BACKEND_ORIGIN=http://backend:8000) and the browser never talks to a
 *   second origin — sidestepping CORS entirely.
 * - NEXT_PUBLIC_API_BASE_URL may override the browser-side base at BUILD
 *   time for direct-to-API deployments (e.g. http://127.0.0.1:8000). It is a
 *   public URL by definition; no credentials ever live in it.
 */

/** @type {import('next').NextConfig} */
const nextConfig = {
  // Standalone output is for containerized/Docker builds; Vercel native builds
  // expect standard serverless output.
  output: process.env.VERCEL ? undefined : "standalone",
  reactStrictMode: true,

  async rewrites() {
    const backendOrigin =
      process.env.BACKEND_ORIGIN && process.env.BACKEND_ORIGIN.trim().length > 0
        ? process.env.BACKEND_ORIGIN.trim().replace(/\/+$/, "")
        : "http://127.0.0.1:8000";
    return [
      {
        source: "/backend/:path*",
        destination: `${backendOrigin}/:path*`,
      },
    ];
  },
};

module.exports = nextConfig;
