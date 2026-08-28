import type { Metadata } from "next";
import Link from "next/link";
import "./globals.css";
import { StatusBar } from "@/components/StatusBar";

export const metadata: Metadata = {
  title: "Sentinel — Financial Research Copilot",
  description:
    "Agentic research over SEC filings, earnings calls, and market news, with cited answers. Research tooling only — not investment advice.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="min-h-screen bg-background text-ink">
        <a
          href="#main-content"
          className="sr-only focus:not-sr-only focus:absolute focus:left-4 focus:top-4 focus:z-50 focus:rounded-md focus:bg-surface focus:px-4 focus:py-2 focus:text-sm focus:font-semibold focus:text-accent focus:shadow-card"
        >
          Skip to content
        </a>

        <div className="mx-auto flex min-h-screen w-full max-w-5xl flex-col px-4 sm:px-6">
          <header className="flex flex-wrap items-center justify-between gap-3 border-b border-line py-4">
            <div className="flex items-baseline gap-3">
              <Link
                href="/"
                className="text-lg font-semibold tracking-tight text-ink transition-enabled hover:text-accent"
              >
                Sentinel
              </Link>
              <span className="hidden text-sm text-ink-faint sm:inline">
                Financial research copilot
              </span>
            </div>
            <nav aria-label="Primary" className="flex items-center gap-1 text-sm font-medium">
              <Link
                href="/"
                className="rounded-md px-3 py-1.5 text-ink-soft transition-enabled hover:bg-surface-muted hover:text-ink"
              >
                Research
              </Link>
              <Link
                href="/sources"
                className="rounded-md px-3 py-1.5 text-ink-soft transition-enabled hover:bg-surface-muted hover:text-ink"
              >
                Sources
              </Link>
            </nav>
            <StatusBar />
          </header>

          <main id="main-content" className="flex-1 py-6">
            {children}
          </main>

          <footer className="border-t border-line py-4 text-xs leading-relaxed text-ink-faint">
            <p>
              Sentinel is a research tool for exploring public filings and market news with cited
              answers. It does not make investment recommendations or trade decisions.
            </p>
          </footer>
        </div>
      </body>
    </html>
  );
}
