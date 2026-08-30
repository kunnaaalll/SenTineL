import type { Metadata } from "next";
import Link from "next/link";
import "./globals.css";
import { StatusBar } from "@/components/StatusBar";
import { BackendGate } from "@/components/BackendGate";
import { SentinelLogo } from "@/components/SentinelLogo";

export const metadata: Metadata = {
  title: "Sentinel — Financial Intelligence Copilot",
  description:
    "Agentic research over SEC filings, earnings calls, and market news, with cited answers. Research tooling only — not investment advice.",
  icons: {
    icon: "/icon.svg",
  },
  openGraph: {
    title: "Sentinel — Financial Intelligence Copilot",
    description: "Agentic research over SEC filings and market news with grounded citations.",
    type: "website",
  },
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="min-h-screen bg-background text-ink antialiased flex flex-col">
        {/* Skip navigation for keyboard accessibility */}
        <a
          href="#main-content"
          className="sr-only focus:not-sr-only focus:absolute focus:left-4 focus:top-4 focus:z-50 focus:rounded-lg focus:bg-surface focus:px-4 focus:py-2.5 focus:text-sm focus:font-semibold focus:text-accent focus:shadow-float focus:border focus:border-accent"
        >
          Skip to content
        </a>

        {/* BackendGate wraps all content — handles conditional cold-start wake-up */}
        <BackendGate>
          <div className="mx-auto flex min-h-screen w-full max-w-5xl flex-col px-4 sm:px-6">
            {/* Header — Sentinel Brand Lockup, Navigation, Status */}
            <header className="flex flex-wrap items-center justify-between gap-4 border-b border-line py-3.5 sm:py-4">
              <div className="flex items-center gap-3">
                <Link
                  href="/"
                  aria-label="Sentinel — home"
                  className="group flex items-center gap-2.5 transition-enabled"
                >
                  <SentinelLogo
                    variant="symbol"
                    size={24}
                    className="transition-transform group-hover:scale-105"
                  />
                  <span className="font-display text-lg font-bold tracking-[0.04em] text-ink transition-enabled group-hover:text-accent">
                    SENTINEL
                  </span>
                </Link>
                <span className="hidden text-xs text-ink-faint sm:inline" aria-hidden>
                  /
                </span>
                <span className="hidden font-sans text-xs font-medium tracking-wide text-ink-soft sm:inline">
                  Financial Intelligence
                </span>
              </div>

              <nav aria-label="Primary" className="flex items-center gap-1 text-sm font-medium">
                <Link
                  href="/"
                  className="rounded-lg px-3 py-1.5 text-ink-soft transition-enabled hover:bg-surface-muted hover:text-ink focus-visible:ring-2 focus-visible:ring-accent"
                >
                  Research
                </Link>
                <Link
                  href="/sources"
                  className="rounded-lg px-3 py-1.5 text-ink-soft transition-enabled hover:bg-surface-muted hover:text-ink focus-visible:ring-2 focus-visible:ring-accent"
                >
                  Sources
                </Link>
              </nav>

              <StatusBar />
            </header>

            <main id="main-content" className="flex-1 flex flex-col py-5 sm:py-6">
              {children}
            </main>

            <footer className="border-t border-line py-4 text-xs leading-relaxed text-ink-faint">
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
