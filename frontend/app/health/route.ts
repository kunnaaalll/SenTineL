import { NextResponse } from "next/server";

/**
 * Liveness endpoint for the frontend container (mirrors the backend's
 * GET /health contract in spirit): always 200 while the server responds.
 * Deep dependency readiness lives on the backend's /ready and is surfaced in
 * the UI via components/StatusBar.tsx — see docs/DEPLOYMENT.md section 5.
 */
export function GET() {
  return NextResponse.json({
    status: "ok",
    service: "sentinel-frontend",
    version: "0.1.0-rc1",
    commit_sha: process.env.NEXT_PUBLIC_COMMIT_SHA || process.env.COMMIT_SHA || "dev",
  });
}
