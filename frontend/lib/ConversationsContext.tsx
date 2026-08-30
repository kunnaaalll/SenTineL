"use client";

import { createContext, useContext, useState, type ReactNode } from "react";
import {
  useConversations,
  type ConversationGroup,
  type StoredConversation,
} from "./useConversations";
import type { ChatMessage } from "@/components/MessageBubble";

interface ConversationsContextValue {
  conversations: StoredConversation[];
  activeConversationId: string | null;
  activeConversation: StoredConversation | null;
  groupedConversations: ConversationGroup[];
  storageError: string | null;
  hasHydrated: boolean;
  isMobileDrawerOpen: boolean;
  isDesktopCollapsed: boolean;
  openMobileDrawer: () => void;
  closeMobileDrawer: () => void;
  toggleDesktopCollapse: () => void;
  startNewChat: () => void;
  selectConversation: (id: string) => void;
  saveActiveMessages: (messages: ChatMessage[]) => void;
  renameConversation: (id: string, newTitle: string) => void;
  deleteConversation: (id: string) => void;
  clearAllConversations: () => void;
}

const ConversationsContext = createContext<ConversationsContextValue | null>(null);

export function ConversationsProvider({ children }: { children: ReactNode }) {
  const convState = useConversations();
  const [isMobileDrawerOpen, setIsMobileDrawerOpen] = useState(false);
  const [isDesktopCollapsed, setIsDesktopCollapsed] = useState(false);

  const openMobileDrawer = () => setIsMobileDrawerOpen(true);
  const closeMobileDrawer = () => setIsMobileDrawerOpen(false);
  const toggleDesktopCollapse = () => setIsDesktopCollapsed((prev) => !prev);

  const value: ConversationsContextValue = {
    ...convState,
    isMobileDrawerOpen,
    isDesktopCollapsed,
    openMobileDrawer,
    closeMobileDrawer,
    toggleDesktopCollapse,
  };

  return <ConversationsContext.Provider value={value}>{children}</ConversationsContext.Provider>;
}

export function useConversationsContext(): ConversationsContextValue {
  const context = useContext(ConversationsContext);
  if (!context) {
    throw new Error("useConversationsContext must be used within a ConversationsProvider");
  }
  return context;
}

export function useOptionalConversationsContext(): ConversationsContextValue | null {
  return useContext(ConversationsContext);
}
