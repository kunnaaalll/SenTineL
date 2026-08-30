import type { Metadata } from "next";
import "./globals.css";
import { BackendGate } from "@/components/BackendGate";
import { AppShell } from "@/components/AppShell";

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

        {/* BackendGate wraps all content — handles conditional cold-start startup */}
        <BackendGate>
          <AppShell>{children}</AppShell>
        </BackendGate>
      </body>
    </html>
  );
}
