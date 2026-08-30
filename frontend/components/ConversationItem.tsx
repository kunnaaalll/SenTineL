"use client";

import { useEffect, useRef, useState, type KeyboardEvent } from "react";
import type { StoredConversation } from "@/lib/useConversations";

export interface ConversationItemProps {
  conversation: StoredConversation;
  isActive: boolean;
  onSelect: (id: string) => void;
  onRename: (id: string, newTitle: string) => void;
  onDelete: (id: string) => void;
}

export function ConversationItem({
  conversation,
  isActive,
  onSelect,
  onRename,
  onDelete,
}: ConversationItemProps) {
  const [isEditing, setIsEditing] = useState(false);
  const [editTitle, setEditTitle] = useState(conversation.title);
  const [isConfirmingDelete, setIsConfirmingDelete] = useState(false);

  const inputRef = useRef<HTMLInputElement | null>(null);
  const deleteTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const selectButtonRef = useRef<HTMLButtonElement | null>(null);

  // Sync edit title if conversation title changes externally
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setEditTitle(conversation.title);
  }, [conversation.title]);

  // Focus input when starting inline rename
  useEffect(() => {
    if (isEditing) {
      inputRef.current?.focus();
      inputRef.current?.select();
    }
  }, [isEditing]);

  // Auto-cancel delete confirmation after 4s
  useEffect(() => {
    if (isConfirmingDelete) {
      deleteTimerRef.current = setTimeout(() => {
        setIsConfirmingDelete(false);
      }, 4000);
    }
    return () => {
      if (deleteTimerRef.current) {
        clearTimeout(deleteTimerRef.current);
        deleteTimerRef.current = null;
      }
    };
  }, [isConfirmingDelete]);

  const commitRename = () => {
    const trimmed = editTitle.trim();
    if (trimmed && trimmed !== conversation.title) {
      onRename(conversation.id, trimmed);
    } else {
      setEditTitle(conversation.title);
    }
    setIsEditing(false);
    selectButtonRef.current?.focus();
  };

  const cancelRename = () => {
    setEditTitle(conversation.title);
    setIsEditing(false);
    selectButtonRef.current?.focus();
  };

  const handleInputKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter") {
      e.preventDefault();
      commitRename();
    } else if (e.key === "Escape") {
      e.preventDefault();
      cancelRename();
    }
  };

  const handleDeleteConfirm = () => {
    if (deleteTimerRef.current) clearTimeout(deleteTimerRef.current);
    setIsConfirmingDelete(false);
    onDelete(conversation.id);
  };

  const handleDeleteCancel = () => {
    if (deleteTimerRef.current) clearTimeout(deleteTimerRef.current);
    setIsConfirmingDelete(false);
    selectButtonRef.current?.focus();
  };

  return (
    <li
      className={`group relative flex items-center justify-between rounded-xl px-2.5 py-2 transition-enabled text-sm ${
        isActive
          ? "bg-surface-muted text-ink font-medium border border-line-strong shadow-xs"
          : "text-ink-soft hover:bg-surface-overlay hover:text-ink border border-transparent"
      }`}
    >
      {isConfirmingDelete ? (
        /* Inline Delete Confirmation State */
        <div
          role="dialog"
          aria-label="Confirm chat deletion"
          className="flex w-full items-center justify-between gap-1.5 animate-fade-up py-0.5"
        >
          <span className="text-xs font-semibold text-danger truncate">Delete this chat?</span>
          <div className="flex items-center gap-1 shrink-0">
            <button
              type="button"
              onClick={handleDeleteConfirm}
              className="transition-enabled rounded bg-danger px-2 py-0.5 text-xs font-semibold text-white hover:bg-danger-soft hover:text-danger focus-visible:ring-2 focus-visible:ring-danger"
              aria-label="Confirm delete conversation"
            >
              Delete
            </button>
            <button
              type="button"
              onClick={handleDeleteCancel}
              className="transition-enabled rounded border border-line bg-surface px-2 py-0.5 text-xs font-medium text-ink-soft hover:text-ink focus-visible:ring-2 focus-visible:ring-accent"
              aria-label="Cancel delete conversation"
            >
              Cancel
            </button>
          </div>
        </div>
      ) : isEditing ? (
        /* Inline Rename Input State */
        <div className="flex w-full items-center gap-1.5 py-0.5">
          <input
            ref={inputRef}
            type="text"
            value={editTitle}
            onChange={(e) => setEditTitle(e.target.value)}
            onKeyDown={handleInputKeyDown}
            onBlur={commitRename}
            maxLength={80}
            className="w-full rounded border border-accent bg-surface px-2 py-1 text-xs text-ink focus:outline-none focus-visible:ring-1 focus-visible:ring-accent"
            aria-label="Edit conversation title"
          />
        </div>
      ) : (
        /* Normal State */
        <>
          <button
            ref={selectButtonRef}
            type="button"
            onClick={() => onSelect(conversation.id)}
            aria-current={isActive ? "page" : undefined}
            className="flex flex-1 items-center gap-2 overflow-hidden text-left focus:outline-none focus-visible:underline"
            title={conversation.title}
          >
            {isActive && (
              <span aria-hidden className="h-1.5 w-1.5 shrink-0 rounded-full bg-accent" />
            )}
            <span className="truncate text-xs leading-snug">{conversation.title}</span>
          </button>

          {/* Action Icons (Fade in on hover / focus-within) */}
          <div className="flex items-center gap-1 opacity-0 group-hover:opacity-100 group-focus-within:opacity-100 transition-opacity shrink-0 ml-1.5">
            <button
              type="button"
              onClick={(e) => {
                e.stopPropagation();
                setIsEditing(true);
              }}
              aria-label={`Rename ${conversation.title}`}
              className="rounded p-1 text-ink-faint hover:bg-surface hover:text-ink focus-visible:opacity-100 focus-visible:ring-1 focus-visible:ring-accent transition-enabled"
            >
              <PencilIcon className="h-3.5 w-3.5" />
            </button>
            <button
              type="button"
              onClick={(e) => {
                e.stopPropagation();
                setIsConfirmingDelete(true);
              }}
              aria-label={`Delete ${conversation.title}`}
              className="rounded p-1 text-ink-faint hover:bg-surface hover:text-danger focus-visible:opacity-100 focus-visible:ring-1 focus-visible:ring-danger transition-enabled"
            >
              <TrashIcon className="h-3.5 w-3.5" />
            </button>
          </div>
        </>
      )}
    </li>
  );
}

function PencilIcon({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 16 16" fill="none" className={className} aria-hidden>
      <path
        d="M11.5 2.5a1.414 1.414 0 0 1 2 2L5 13H2.5v-2.5l8.5-8.5Z"
        stroke="currentColor"
        strokeWidth="1.25"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function TrashIcon({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 16 16" fill="none" className={className} aria-hidden>
      <path
        d="M2.5 4h11m-9.5 0v8.5a1 1 0 0 0 1 1h6a1 1 0 0 0 1-1V4M5.5 4V2.5a1 1 0 0 1 1-1h3a1 1 0 0 1 1 1V4"
        stroke="currentColor"
        strokeWidth="1.25"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}
