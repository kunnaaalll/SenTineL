"use client";

import { type ReactNode } from "react";
import Link from "next/link";
import { StatusBar } from "@/components/StatusBar";
import { SentinelLogo } from "@/components/SentinelLogo";
import { Sidebar } from "@/components/Sidebar";
import { ConversationsProvider, useConversationsContext } from "@/lib/ConversationsContext";

function AppShellInner({ children }: { children: ReactNode }) {
  const {
    groupedConversations,
    activeConversationId,
    storageError,
    isMobileDrawerOpen,
    isDesktopCollapsed,
    openMobileDrawer,
    closeMobileDrawer,
    toggleDesktopCollapse,
    startNewChat,
    selectConversation,
    renameConversation,
    deleteConversation,
  } = useConversationsContext();

  return (
    <div className="flex h-screen w-full overflow-hidden bg-background">
      {/* Sidebar: Desktop persistent 280px / 56px rail + Mobile drawer */}
      <Sidebar
        groups={groupedConversations}
        activeId={activeConversationId}
        storageError={storageError}
        isOpenMobile={isMobileDrawerOpen}
        onCloseMobile={closeMobileDrawer}
        isCollapsedDesktop={isDesktopCollapsed}
        onToggleCollapseDesktop={toggleDesktopCollapse}
        onNewChat={startNewChat}
        onSelectConversation={selectConversation}
        onRenameConversation={renameConversation}
        onDeleteConversation={deleteConversation}
      />

      {/* Main Content Area: Fixed Header + Scrollable Main Viewport */}
      <div className="flex flex-1 flex-col h-screen overflow-hidden min-w-0">
        {/* Fixed Header */}
        <header className="shrink-0 z-30 flex h-14 w-full items-center justify-between border-b border-line bg-background/95 backdrop-blur-md px-4 sm:px-6">
          <div className="mx-auto flex w-full max-w-5xl items-center justify-between gap-3">
            <div className="flex items-center gap-2.5">
              {/* Mobile Drawer Hamburger Button */}
              <button
                type="button"
                onClick={openMobileDrawer}
                aria-label="Open chat sessions history"
                className="flex lg:hidden rounded-lg p-1.5 text-ink-soft hover:bg-surface-muted hover:text-ink focus-visible:ring-2 focus-visible:ring-accent transition-enabled"
              >
                <HamburgerIcon className="h-5 w-5" />
              </button>

              {/* Brand Lockup */}
              <Link
                href="/"
                aria-label="Sentinel — home"
                className="group flex items-center gap-2 transition-enabled"
              >
                <SentinelLogo
                  variant="symbol"
                  size={22}
                  className="transition-transform group-hover:scale-105"
                />
                <span className="font-display text-base font-bold tracking-[0.04em] text-ink transition-enabled group-hover:text-accent sm:text-lg">
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

            {/* Primary Nav Links */}
            <div className="flex items-center gap-3">
              <nav aria-label="Primary" className="flex items-center gap-1 text-sm font-medium">
                <Link
                  href="/"
                  className="rounded-lg px-2.5 py-1 text-xs sm:text-sm text-ink-soft transition-enabled hover:bg-surface-muted hover:text-ink focus-visible:ring-2 focus-visible:ring-accent"
                >
                  Research
                </Link>
                <Link
                  href="/sources"
                  className="rounded-lg px-2.5 py-1 text-xs sm:text-sm text-ink-soft transition-enabled hover:bg-surface-muted hover:text-ink focus-visible:ring-2 focus-visible:ring-accent"
                >
                  Sources
                </Link>
              </nav>

              <StatusBar />
            </div>
          </div>
        </header>

        {/* Main Viewport */}
        <main id="main-content" className="flex-1 overflow-hidden min-h-0 relative flex flex-col">
          <div className="mx-auto flex w-full max-w-5xl flex-1 flex-col h-full min-h-0 px-4 sm:px-6">
            {children}
          </div>
        </main>

        {/* Accessible Disclaimer (visually hidden) */}
        <footer className="sr-only">
          <p>
            Sentinel is a research tool for exploring public SEC filings and market news with cited
            answers. It does not provide investment advice or make trading decisions.
          </p>
        </footer>
      </div>
    </div>
  );
}

export function AppShell({ children }: { children: ReactNode }) {
  return (
    <ConversationsProvider>
      <AppShellInner>{children}</AppShellInner>
    </ConversationsProvider>
  );
}

function HamburgerIcon({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 16 16" fill="none" className={className} aria-hidden>
      <path
        d="M2.5 4.5h11M2.5 8h11M2.5 11.5h11"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinecap="round"
      />
    </svg>
  );
}
