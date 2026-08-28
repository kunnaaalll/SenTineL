import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MessageBubble, type ChatMessage } from "@/components/MessageBubble";
import { citationFixture, queryResponseFixture } from "./helpers";

const completeMessage: ChatMessage = {
  id: "a1",
  role: "assistant",
  status: "complete",
  ...queryResponseFixture(),
};

describe("message rendering", () => {
  it("styles user questions distinctly from assistant answers", () => {
    const { container, unmount } = render(
      <MessageBubble
        message={{ id: "u1", role: "user", question: "Compare AAPL and MSFT", status: "complete" }}
      />,
    );
    const userArticle = screen.getByRole("article", { name: /your question/i });
    expect(userArticle.firstChild).toHaveClass("bg-ink");
    unmount();

    render(<MessageBubble message={completeMessage} />);
    expect(screen.getByRole("article", { name: /answer from sentinel/i }).firstChild).toHaveClass(
      "bg-surface",
    );
    void container;
  });

  it("renders markdown structure without executing raw HTML", () => {
    render(
      <MessageBubble
        message={{
          ...completeMessage,
          citations: [],
          agent_path: [],
          answer: "**Gross margin** expanded.\n\n- Point one [1]\n\n<script>alert('xss')</script>",
        }}
      />,
    );
    // Markdown emphasis renders as a <strong>, list items as <li>…
    expect(screen.getByText(/gross margin/i).tagName).toBe("STRONG");
    expect(screen.getByRole("list")).toBeInTheDocument();
    // …while injected script tags stay inert text.
    expect(document.querySelector("script")).toBeNull();
    expect(screen.getByText(/<script>/i)).toBeInTheDocument();
  });

  it("renders comparison tables produced by multi-hop answers", () => {
    render(
      <MessageBubble
        message={{
          ...completeMessage,
          citations: [],
          agent_path: [],
          answer: "| Metric | AAPL | MSFT |\n|---|---|---|\n| Revenue | $391B | $245B |",
        }}
      />,
    );
    expect(screen.getByRole("table")).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "AAPL" })).toBeInTheDocument();
  });

  it("turns inline [1] markers into buttons that expand the matching source card", async () => {
    const user = userEvent.setup();
    render(<MessageBubble message={completeMessage} />);

    const marker = screen.getByRole("button", { name: /show source 1/i });
    expect(screen.queryByRole("region", { name: /apple inc\. 10-k/i })).not.toBeInTheDocument();

    await user.click(marker);
    expect(screen.getByRole("region", { name: /apple inc\. 10-k/i })).toBeInTheDocument();

    // Toggling the same marker collapses the card again.
    await user.click(screen.getByRole("button", { name: /show source 1/i }));
    expect(screen.queryByRole("region", { name: /apple inc\. 10-k/i })).not.toBeInTheDocument();
  });

  it("keeps out-of-range citation markers visible instead of dropping them", () => {
    render(<MessageBubble message={{ ...completeMessage, citations: [] }} />);
    // No valid citation exists, so "[1]" renders literally (styled, not a button).
    expect(screen.queryByRole("button", { name: /show source/i })).not.toBeInTheDocument();
    expect(screen.getByText("[1]")).toBeInTheDocument();
  });
});

describe("evidence states", () => {
  it("flags insufficient-evidence refusals with an actionable notice", () => {
    render(
      <MessageBubble
        message={{
          ...completeMessage,
          citations: [],
          agent_path: ["classify"],
          answer: "Insufficient evidence was retrieved to answer this question.",
        }}
      />,
    );
    const notice = screen.getByRole("complementary", { name: /no supporting evidence/i });
    expect(notice).toHaveTextContent(/sources page/i);
  });

  it("does not flag cited answers as insufficient evidence", () => {
    render(<MessageBubble message={completeMessage} />);
    expect(
      screen.queryByRole("complementary", { name: /no supporting evidence/i }),
    ).not.toBeInTheDocument();
  });

  it("separates trailing Limitations sections into a distinct caveat panel", () => {
    render(
      <MessageBubble
        message={{
          ...completeMessage,
          citations: [citationFixture()],
          answer:
            "Revenue grew 2% year over year [1].\nLimitations:\nNews coverage for Q3 was unavailable.",
        }}
      />,
    );
    const panel = screen.getByRole("complementary", { name: /limitations of this answer/i });
    expect(panel).toHaveTextContent(/news coverage for q3 was unavailable/i);
    expect(panel).not.toHaveTextContent(/revenue grew 2%/i);
  });

  it("shows cancellation and error variants", () => {
    const { unmount } = render(
      <MessageBubble
        message={{ id: "c1", role: "assistant", status: "canceled", question: "Hi" }}
      />,
    );
    expect(screen.getByRole("article", { name: /request canceled/i })).toBeInTheDocument();
    unmount();

    render(
      <MessageBubble
        message={{
          id: "e1",
          role: "assistant",
          status: "error",
          errorMessage: "Could not reach the Sentinel backend.",
          errorCode: "http_error",
        }}
      />,
    );
    const errorArticle = screen.getByRole("article", { name: /the request failed/i });
    expect(errorArticle).toHaveTextContent(/could not reach the sentinel backend/i);
    expect(errorArticle).toHaveTextContent(/error code: http_error/);
  });
});
