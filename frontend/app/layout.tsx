import type { Metadata } from "next";
import Link from "next/link";
import "./globals.css";
import { StatusBar } from "@/components/StatusBar";
import { BackendGate } from "@/components/BackendGate";

export const metadata: Metadata = {
  title: "Sentinel — Financial Research Copilot",
  description:
    "Agentic research over SEC filings, earnings calls, and market news, with cited answers. Research tooling only — not investment advice.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="min-h-screen bg-background text-ink">
        {/* Skip navigation for keyboard users */}
        <a
          href="#main-content"
          className="sr-only focus:not-sr-only focus:absolute focus:left-4 focus:top-4 focus:z-50 focus:rounded-lg focus:bg-surface focus:px-4 focus:py-2 focus:text-sm focus:font-semibold focus:text-accent focus:shadow-card"
        >
          Skip to content
        </a>

        {/* BackendGate wraps all content — shows wake-up screen on cold start */}
        <BackendGate>
          <div className="mx-auto flex min-h-screen w-full max-w-5xl flex-col px-4 sm:px-6">
            {/* Header — Sentinel wordmark, navigation, status pill */}
            <header className="flex flex-wrap items-center justify-between gap-3 border-b border-line py-4">
              <div className="flex items-center gap-3">
                {/* Wordmark with gold accent dot */}
                <Link
                  href="/"
                  aria-label="Sentinel — home"
                  className="flex items-center gap-2 group"
                >
                  {/* Orb / signal indicator */}
                  <span
                    aria-hidden
                    className="h-2 w-2 rounded-full bg-accent opacity-80 transition-enabled group-hover:opacity-100"
                    style={{
                      boxShadow: "0 0 8px rgba(200,160,48,0.5)",
                    }}
                  />
                  <span className="font-display text-lg font-semibold tracking-tight text-ink transition-enabled group-hover:text-accent">
                    Sentinel
                  </span>
                </Link>
                <span className="hidden text-sm text-ink-faint sm:inline" aria-hidden>
                  /
                </span>
                <span className="hidden text-sm text-ink-faint sm:inline">
                  Financial research copilot
                </span>
              </div>

              <nav aria-label="Primary" className="flex items-center gap-0.5 text-sm font-medium">
                <Link
                  href="/"
                  className="rounded-lg px-3 py-1.5 text-ink-soft transition-enabled hover:bg-surface-raised hover:text-ink"
                >
                  Research
                </Link>
                <Link
                  href="/sources"
                  className="rounded-lg px-3 py-1.5 text-ink-soft transition-enabled hover:bg-surface-raised hover:text-ink"
                >
                  Sources
                </Link>
              </nav>

              <StatusBar />
            </header>

            <main id="main-content" className="flex-1 py-6">
              {children}
            </main>

            <footer className="border-t border-line py-5 text-xs leading-relaxed text-ink-faint">
              <p className="m-0">
                Sentinel is a research tool for exploring public SEC filings and market news with
                cited answers. It does not provide investment advice or make trading decisions.
              </p>
            </footer>
          </div>
        </BackendGate>
      </body>
    </html>
  );
}
