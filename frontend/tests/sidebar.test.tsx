/**
 * tests/sidebar.test.tsx
 *
 * Offline component tests for Sidebar and ConversationItem.
 * Tests desktop persistent mode, collapsed rail, mobile drawer, grouping,
 * active indicator, inline rename, and inline delete confirmation.
 */

import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, act } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Sidebar } from "@/components/Sidebar";
import type { ConversationGroup, StoredConversation } from "@/lib/useConversations";

function createMockConversation(id: string, title: string): StoredConversation {
  return {
    id,
    title,
    titleIsCustom: false,
    createdAt: new Date().toISOString(),
    updatedAt: new Date().toISOString(),
    messages: [],
  };
}

const mockGroups: ConversationGroup[] = [
  {
    label: "Today",
    conversations: [
      createMockConversation("c1", "Apple 10-K Net Sales Analysis"),
      createMockConversation("c2", "Tesla Risk Factors Summary"),
    ],
  },
  {
    label: "Yesterday",
    conversations: [createMockConversation("c3", "NVIDIA Data Center Growth")],
  },
];

describe("Sidebar component", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("renders desktop sidebar with grouped conversation items and New Chat button", () => {
    render(
      <Sidebar
        groups={mockGroups}
        activeId="c1"
        storageError={null}
        isOpenMobile={false}
        onCloseMobile={vi.fn()}
        isCollapsedDesktop={false}
        onToggleCollapseDesktop={vi.fn()}
        onNewChat={vi.fn()}
        onSelectConversation={vi.fn()}
        onRenameConversation={vi.fn()}
        onDeleteConversation={vi.fn()}
      />,
    );

    expect(screen.getByRole("button", { name: /new research session/i })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Today" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Yesterday" })).toBeInTheDocument();
    expect(screen.getByText("Apple 10-K Net Sales Analysis")).toBeInTheDocument();
    expect(screen.getByText("NVIDIA Data Center Growth")).toBeInTheDocument();

    // Active item has aria-current="page"
    const activeItem = screen.getByRole("button", {
      name: "Apple 10-K Net Sales Analysis",
    });
    expect(activeItem).toHaveAttribute("aria-current", "page");
  });

  it("renders collapsed desktop rail when isCollapsedDesktop is true", () => {
    render(
      <Sidebar
        groups={mockGroups}
        activeId="c1"
        storageError={null}
        isOpenMobile={false}
        onCloseMobile={vi.fn()}
        isCollapsedDesktop={true}
        onToggleCollapseDesktop={vi.fn()}
        onNewChat={vi.fn()}
        onSelectConversation={vi.fn()}
        onRenameConversation={vi.fn()}
        onDeleteConversation={vi.fn()}
      />,
    );

    expect(screen.getByRole("button", { name: /expand sidebar/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /new chat/i })).toBeInTheDocument();
    expect(screen.queryByText("Apple 10-K Net Sales Analysis")).toBeNull();
  });

  it("triggers onNewChat when New Chat button is clicked", async () => {
    const user = userEvent.setup();
    const onNewChat = vi.fn();
    render(
      <Sidebar
        groups={mockGroups}
        activeId="c1"
        storageError={null}
        isOpenMobile={false}
        onCloseMobile={vi.fn()}
        isCollapsedDesktop={false}
        onToggleCollapseDesktop={vi.fn()}
        onNewChat={onNewChat}
        onSelectConversation={vi.fn()}
        onRenameConversation={vi.fn()}
        onDeleteConversation={vi.fn()}
      />,
    );

    await user.click(screen.getByRole("button", { name: /new research session/i }));
    expect(onNewChat).toHaveBeenCalledTimes(1);
  });

  it("triggers onSelectConversation when a session is clicked", async () => {
    const user = userEvent.setup();
    const onSelect = vi.fn();
    render(
      <Sidebar
        groups={mockGroups}
        activeId="c1"
        storageError={null}
        isOpenMobile={false}
        onCloseMobile={vi.fn()}
        isCollapsedDesktop={false}
        onToggleCollapseDesktop={vi.fn()}
        onNewChat={vi.fn()}
        onSelectConversation={onSelect}
        onRenameConversation={vi.fn()}
        onDeleteConversation={vi.fn()}
      />,
    );

    await user.click(screen.getByRole("button", { name: "Tesla Risk Factors Summary" }));
    expect(onSelect).toHaveBeenCalledWith("c2");
  });

  it("supports inline rename via keyboard", async () => {
    const user = userEvent.setup();
    const onRename = vi.fn();
    render(
      <Sidebar
        groups={mockGroups}
        activeId="c1"
        storageError={null}
        isOpenMobile={false}
        onCloseMobile={vi.fn()}
        isCollapsedDesktop={false}
        onToggleCollapseDesktop={vi.fn()}
        onNewChat={vi.fn()}
        onSelectConversation={vi.fn()}
        onRenameConversation={onRename}
        onDeleteConversation={vi.fn()}
      />,
    );

    const renameBtn = screen.getByRole("button", {
      name: /rename apple 10-k net sales analysis/i,
    });
    await user.click(renameBtn);

    const input = screen.getByRole("textbox", { name: /edit conversation title/i });
    expect(input).toHaveValue("Apple 10-K Net Sales Analysis");

    await user.clear(input);
    await user.type(input, "Apple FY24 Revenue Deep Dive{Enter}");

    expect(onRename).toHaveBeenCalledWith("c1", "Apple FY24 Revenue Deep Dive");
  });

  it("shows inline delete confirmation and allows cancelling or confirming", async () => {
    const user = userEvent.setup();
    const onDelete = vi.fn();
    render(
      <Sidebar
        groups={mockGroups}
        activeId="c1"
        storageError={null}
        isOpenMobile={false}
        onCloseMobile={vi.fn()}
        isCollapsedDesktop={false}
        onToggleCollapseDesktop={vi.fn()}
        onNewChat={vi.fn()}
        onSelectConversation={vi.fn()}
        onRenameConversation={vi.fn()}
        onDeleteConversation={onDelete}
      />,
    );

    const deleteBtn = screen.getByRole("button", {
      name: /delete apple 10-k net sales analysis/i,
    });
    await user.click(deleteBtn);

    expect(screen.getByText(/delete this chat\?/i)).toBeInTheDocument();
    const cancelBtn = screen.getByRole("button", { name: /cancel delete conversation/i });

    // Cancel first
    await user.click(cancelBtn);
    expect(screen.queryByText(/delete this chat\?/i)).toBeNull();

    // Click delete again and confirm
    const deleteBtnAgain = screen.getByRole("button", {
      name: /delete apple 10-k net sales analysis/i,
    });
    await user.click(deleteBtnAgain);
    const confirmBtn = screen.getByRole("button", { name: /confirm delete conversation/i });
    await user.click(confirmBtn);
    expect(onDelete).toHaveBeenCalledWith("c1");
  });

  it("renders mobile drawer when isOpenMobile is true and closes on Escape", async () => {
    const onCloseMobile = vi.fn();
    render(
      <Sidebar
        groups={mockGroups}
        activeId="c1"
        storageError={null}
        isOpenMobile={true}
        onCloseMobile={onCloseMobile}
        isCollapsedDesktop={false}
        onToggleCollapseDesktop={vi.fn()}
        onNewChat={vi.fn()}
        onSelectConversation={vi.fn()}
        onRenameConversation={vi.fn()}
        onDeleteConversation={vi.fn()}
      />,
    );

    expect(screen.getByRole("dialog", { name: /chat history drawer/i })).toBeInTheDocument();

    act(() => {
      window.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" }));
    });

    expect(onCloseMobile).toHaveBeenCalledTimes(1);
  });

  it("displays non-blocking storage error notice when present", () => {
    render(
      <Sidebar
        groups={mockGroups}
        activeId="c1"
        storageError="Couldn't save this chat — your browser's local storage may be full or disabled."
        isOpenMobile={false}
        onCloseMobile={vi.fn()}
        isCollapsedDesktop={false}
        onToggleCollapseDesktop={vi.fn()}
        onNewChat={vi.fn()}
        onSelectConversation={vi.fn()}
        onRenameConversation={vi.fn()}
        onDeleteConversation={vi.fn()}
      />,
    );

    expect(screen.getByRole("alert")).toHaveTextContent(
      /couldn't save this chat — your browser's local storage may be full/i,
    );
  });
});
