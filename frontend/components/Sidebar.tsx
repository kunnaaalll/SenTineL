"use client";

import { useEffect, useRef } from "react";
import type { ConversationGroup, StoredConversation } from "@/lib/useConversations";
import { ConversationItem } from "./ConversationItem";
import { SentinelLogo } from "./SentinelLogo";

export interface SidebarProps {
  groups: ConversationGroup[];
  activeId: string | null;
  storageError: string | null;
  isOpenMobile: boolean;
  onCloseMobile: () => void;
  isCollapsedDesktop: boolean;
  onToggleCollapseDesktop: () => void;
  onNewChat: () => void;
  onSelectConversation: (id: string) => void;
  onRenameConversation: (id: string, newTitle: string) => void;
  onDeleteConversation: (id: string) => void;
}

export function Sidebar({
  groups,
  activeId,
  storageError,
  isOpenMobile,
  onCloseMobile,
  isCollapsedDesktop,
  onToggleCollapseDesktop,
  onNewChat,
  onSelectConversation,
  onRenameConversation,
  onDeleteConversation,
}: SidebarProps) {
  const drawerRef = useRef<HTMLDivElement | null>(null);

  // Close mobile drawer on Escape
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape" && isOpenMobile) {
        onCloseMobile();
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [isOpenMobile, onCloseMobile]);

  const handleSelect = (id: string) => {
    onSelectConversation(id);
    if (isOpenMobile) {
      onCloseMobile();
    }
  };

  const handleNewChatClick = () => {
    onNewChat();
    if (isOpenMobile) {
      onCloseMobile();
    }
  };

  const totalConversations = groups.reduce((acc, g) => acc + g.conversations.length, 0);

  // 1. Collapsed Desktop Rail (56px)
  if (isCollapsedDesktop) {
    return (
      <aside
        aria-label="Chat history rail"
        className="hidden lg:flex w-14 flex-col items-center justify-between border-r border-line bg-surface py-4 shrink-0 transition-all duration-200"
      >
        <div className="flex flex-col items-center gap-3 w-full">
          {/* Expand sidebar toggle button */}
          <button
            type="button"
            onClick={onToggleCollapseDesktop}
            aria-label="Expand sidebar"
            className="rounded-lg p-2 text-ink-soft hover:bg-surface-muted hover:text-ink focus-visible:ring-2 focus-visible:ring-accent transition-enabled"
            title="Expand sidebar"
          >
            <SidebarExpandIcon className="h-5 w-5" />
          </button>

          {/* New Chat icon button */}
          <button
            type="button"
            onClick={handleNewChatClick}
            aria-label="New chat"
            className="rounded-lg bg-accent-soft border border-accent/30 p-2 text-accent hover:bg-accent hover:text-on-accent focus-visible:ring-2 focus-visible:ring-accent transition-enabled"
            title="New chat"
          >
            <PlusIcon className="h-4 w-4" />
          </button>
        </div>

        {/* Small bottom brand symbol */}
        <div className="p-1 text-ink-faint">
          <SentinelLogo variant="compact" size={18} />
        </div>
      </aside>
    );
  }

  // 2. Full Sidebar Content (Used for persistent desktop & mobile off-canvas drawer)
  const sidebarContent = (
    <div className="flex h-full w-full flex-col justify-between overflow-hidden bg-surface p-3.5">
      {/* Top Header + New Chat Button */}
      <div className="flex flex-col gap-3">
        <div className="flex items-center justify-between px-1">
          <div className="flex items-center gap-2">
            <SentinelLogo variant="symbol" size={20} />
            <span className="font-display text-sm font-bold tracking-[0.04em] text-ink">
              Research Sessions
            </span>
          </div>

          {/* Desktop collapse toggle */}
          <button
            type="button"
            onClick={onToggleCollapseDesktop}
            aria-label="Collapse sidebar"
            className="hidden lg:flex rounded-md p-1.5 text-ink-faint hover:bg-surface-muted hover:text-ink transition-enabled"
            title="Collapse sidebar"
          >
            <SidebarCollapseIcon className="h-4 w-4" />
          </button>

          {/* Mobile close toggle */}
          <button
            type="button"
            onClick={onCloseMobile}
            aria-label="Close menu"
            className="flex lg:hidden rounded-md p-1.5 text-ink-faint hover:bg-surface-muted hover:text-ink transition-enabled"
          >
            <CloseIcon className="h-4 w-4" />
          </button>
        </div>

        {/* New Chat Button */}
        <button
          type="button"
          onClick={handleNewChatClick}
          className="flex w-full items-center justify-center gap-2 rounded-xl bg-accent px-3.5 py-2 text-xs font-semibold text-on-accent shadow-xs hover:bg-accent-strong focus-visible:ring-2 focus-visible:ring-accent transition-enabled"
        >
          <PlusIcon className="h-3.5 w-3.5" />
          <span>New research session</span>
        </button>
      </div>

      {/* Storage Error Toast */}
      {storageError && (
        <div
          role="alert"
          className="my-2 rounded-lg border border-warning/40 bg-warning-soft p-2.5 text-[11px] leading-tight text-warning-ink"
        >
          {storageError}
        </div>
      )}

      {/* Grouped Conversation History */}
      <nav aria-label="Chat history" className="my-3 flex-1 overflow-y-auto pr-1 space-y-4">
        {totalConversations === 0 ? (
          <div className="py-8 text-center text-xs text-ink-faint">
            <p className="m-0">No saved sessions yet.</p>
            <p className="mt-1 text-[11px] text-ink-faint/80">
              Start asking questions to build your research log.
            </p>
          </div>
        ) : (
          groups.map((group) => (
            <div key={group.label} className="space-y-1">
              <h3 className="px-2 font-mono text-[10px] font-bold uppercase tracking-wider text-ink-faint">
                {group.label}
              </h3>
              <ul className="m-0 list-none space-y-0.5 p-0">
                {group.conversations.map((conv: StoredConversation) => (
                  <ConversationItem
                    key={conv.id}
                    conversation={conv}
                    isActive={conv.id === activeId}
                    onSelect={handleSelect}
                    onRename={onRenameConversation}
                    onDelete={onDeleteConversation}
                  />
                ))}
              </ul>
            </div>
          ))
        )}
      </nav>

      {/* Footer info */}
      <div className="border-t border-line pt-2.5 text-center text-[10px] text-ink-faint">
        <span className="font-mono">{totalConversations}</span> saved session
        {totalConversations === 1 ? "" : "s"} · Local only
      </div>
    </div>
  );

  return (
    <>
      {/* Desktop Persistent Sidebar (280px) */}
      <aside
        aria-label="Chat history"
        className="hidden lg:flex w-[280px] flex-col border-r border-line bg-surface shrink-0 h-full transition-all duration-200"
      >
        {sidebarContent}
      </aside>

      {/* Mobile / Tablet Off-Canvas Drawer Overlay */}
      {isOpenMobile && (
        <div
          className="fixed inset-0 z-50 flex lg:hidden"
          role="dialog"
          aria-modal="true"
          aria-label="Chat history drawer"
        >
          {/* Scrim backdrop */}
          <div
            className="fixed inset-0 bg-ink/45 backdrop-blur-[2px] transition-opacity"
            onClick={onCloseMobile}
            aria-hidden="true"
          />

          {/* Slide-out drawer panel */}
          <div
            ref={drawerRef}
            className="relative flex w-[280px] max-w-[85vw] flex-col border-r border-line bg-surface shadow-float h-full z-10 animate-fade-up"
          >
            {sidebarContent}
          </div>
        </div>
      )}
    </>
  );
}

function PlusIcon({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 16 16" fill="none" className={className} aria-hidden>
      <path d="M8 3v10M3 8h10" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
    </svg>
  );
}

function CloseIcon({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 16 16" fill="none" className={className} aria-hidden>
      <path d="M4 4l8 8M12 4l-8 8" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
    </svg>
  );
}

function SidebarCollapseIcon({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 16 16" fill="none" className={className} aria-hidden>
      <rect x="2" y="2" width="12" height="12" rx="2" stroke="currentColor" strokeWidth="1.2" />
      <path
        d="M6 2v12M11 6l-2 2 2 2"
        stroke="currentColor"
        strokeWidth="1.2"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function SidebarExpandIcon({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 16 16" fill="none" className={className} aria-hidden>
      <rect x="2" y="2" width="12" height="12" rx="2" stroke="currentColor" strokeWidth="1.2" />
      <path
        d="M6 2v12M9 6l2 2-2 2"
        stroke="currentColor"
        strokeWidth="1.2"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}
