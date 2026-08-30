import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor, act } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ChatWindow } from "@/components/ChatWindow";
import {
  errorEnvelope,
  fetchThatHonorsAbort,
  jsonResponse,
  queryResponseFixture,
  stubFetch,
  stubLocalStorage,
} from "./helpers";
import { STORAGE_KEY, SCHEMA_VERSION } from "@/lib/persistence";

// Provide a BackendContext with ready status so tests don't need BackendGate fetch
function ChatWindowWrapper() {
  return <ChatWindow />;
}

function announcerText(container: HTMLElement): string {
  return container.querySelector<HTMLElement>(".sr-only")?.textContent ?? "";
}

describe("empty state", () => {
  beforeEach(() => {
    stubLocalStorage();
  });
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("offers example research questions that load into the input", async () => {
    const user = userEvent.setup();
    const { container } = render(<ChatWindowWrapper />);

    expect(screen.getByRole("heading", { name: /ask a research question/i })).toBeInTheDocument();
    const examples = screen.getAllByRole("button", { name: /apple|compare|tesla|nvidia/i });
    expect(examples.length).toBeGreaterThanOrEqual(4);

    await user.click(screen.getByRole("button", { name: /Apple's total net sales/i }));
    expect(screen.getByRole("textbox", { name: /your question/i })).toHaveValue(
      "What was Apple's total net sales in fiscal 2024?",
    );
    void container;
  });

  it("keeps the submit button disabled while the input is empty", () => {
    render(<ChatWindowWrapper />);
    expect(screen.getByRole("button", { name: /ask sentinel/i })).toBeDisabled();
  });
});

describe("query submission", () => {
  beforeEach(() => {
    stubLocalStorage();
  });
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("posts the question to /query and renders the cited answer", async () => {
    const user = userEvent.setup();
    const calls = stubFetch(() => jsonResponse(queryResponseFixture()));
    render(<ChatWindowWrapper />);

    await user.type(
      screen.getByRole("textbox", { name: /your question/i }),
      "What was Apple's revenue?",
    );
    await user.click(screen.getByRole("button", { name: /ask sentinel/i }));

    // The user turn appears immediately.
    expect(screen.getByRole("article", { name: /your question/i })).toHaveTextContent(
      "What was Apple's revenue?",
    );

    await waitFor(() => {
      expect(screen.getByRole("article", { name: /answer from sentinel/i })).toBeInTheDocument();
    });
    expect(calls[0]?.url).toBe("/query");
    expect(JSON.parse(String(calls[0]?.init.body))).toEqual({
      question: "What was Apple's revenue?",
    });
    expect(await screen.findByText(/\$391,035 million/)).toBeInTheDocument();
  });

  it("submits on Enter without a modifier and inserts a newline on Shift+Enter", async () => {
    const user = userEvent.setup();
    const calls = stubFetch(() => jsonResponse(queryResponseFixture()));
    render(<ChatWindowWrapper />);

    const input = screen.getByRole("textbox", { name: /your question/i });
    await user.type(input, "First question");
    await user.keyboard("{Enter}");

    await waitFor(() => expect(calls.length).toBe(1));

    // After submit the input cleared; type again and Shift+Enter must NOT post.
    await user.type(input, "Second question");
    await user.keyboard("{Shift>}{Enter}{/Shift}");
    await waitFor(() => expect(calls.length).toBe(1));
    expect(input).toHaveValue("Second question\n");
  });

  it("routes through /agents/query when multi-agent mode is forced", async () => {
    const user = userEvent.setup();
    const calls = stubFetch(() =>
      jsonResponse(
        queryResponseFixture({ agent_path: ["classify", "fetch", "extract", "synthesize"] }),
      ),
    );
    render(<ChatWindowWrapper />);

    await user.click(screen.getByRole("checkbox", { name: /force multi-agent analysis/i }));
    await user.type(
      screen.getByRole("textbox", { name: /your question/i }),
      "Compare AAPL and MSFT",
    );
    await user.click(screen.getByRole("button", { name: /ask sentinel/i }));

    await screen.findByRole("article", { name: /answer from sentinel/i });
    expect(calls[0]?.url).toBe("/agents/query");
  });

  it("rejects whitespace-only questions without any request", async () => {
    const user = userEvent.setup();
    const calls = stubFetch(() => jsonResponse(queryResponseFixture()));
    render(<ChatWindowWrapper />);

    await user.type(screen.getByRole("textbox", { name: /your question/i }), "   ");
    expect(screen.getByRole("button", { name: /ask sentinel/i })).toBeDisabled();
    expect(calls).toHaveLength(0);
  });
});

describe("loading and cancellation", () => {
  beforeEach(() => {
    stubLocalStorage();
  });
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("shows a pending indicator while waiting and swaps in the answer", async () => {
    const user = userEvent.setup();
    let resolveFetch!: (response: Response) => void;
    stubFetch(
      () =>
        new Promise<Response>((resolve) => {
          resolveFetch = resolve;
        }),
    );
    render(<ChatWindowWrapper />);

    await user.type(
      screen.getByRole("textbox", { name: /your question/i }),
      "Slow question please",
    );
    await user.click(screen.getByRole("button", { name: /ask sentinel/i }));

    expect(await screen.findByText(/searching sec filings and market news/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^cancel$/i })).toBeInTheDocument();

    await waitFor(() => expect(typeof resolveFetch).toBe("function"));
    resolveFetch(jsonResponse(queryResponseFixture()));

    expect(
      await screen.findByRole("article", { name: /answer from sentinel/i }),
    ).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^cancel$/i })).not.toBeInTheDocument();
  });

  it("marks the exchange canceled when the user presses Cancel mid-flight", async () => {
    const user = userEvent.setup();
    stubFetch(fetchThatHonorsAbort());
    render(<ChatWindowWrapper />);

    await user.type(screen.getByRole("textbox", { name: /your question/i }), "Cancel me");
    await user.click(screen.getByRole("button", { name: /ask sentinel/i }));
    await user.click(await screen.findByRole("button", { name: /^cancel$/i }));

    expect(await screen.findByRole("article", { name: /request canceled/i })).toBeInTheDocument();
  });

  it("cancels the in-flight request when Escape is pressed in the input", async () => {
    const user = userEvent.setup();
    stubFetch(fetchThatHonorsAbort());
    render(<ChatWindowWrapper />);

    await user.type(screen.getByRole("textbox", { name: /your question/i }), "Escape cancels");
    await user.click(screen.getByRole("button", { name: /ask sentinel/i }));
    await screen.findByRole("button", { name: /^cancel$/i });

    await user.type(screen.getByRole("textbox", { name: /your question/i }), "{Escape}");

    expect(await screen.findByRole("article", { name: /request canceled/i })).toBeInTheDocument();
  });

  it("announces progress and completion through the live region", async () => {
    const user = userEvent.setup();
    const { container } = render(<ChatWindowWrapper />);
    let resolveFetch!: (response: Response) => void;
    stubFetch(
      () =>
        new Promise<Response>((resolve) => {
          resolveFetch = resolve;
        }),
    );

    await user.type(screen.getByRole("textbox", { name: /your question/i }), "Announce me");
    await user.click(screen.getByRole("button", { name: /ask sentinel/i }));
    await waitFor(() => expect(announcerText(container)).toMatch(/researching your question/i));

    await waitFor(() => expect(typeof resolveFetch).toBe("function"));
    resolveFetch(jsonResponse(queryResponseFixture()));
    await waitFor(() => expect(announcerText(container)).toMatch(/answer ready/i));
  });

  it("renders a curated error state when the backend answers 503 degraded", async () => {
    const user = userEvent.setup();
    stubFetch(() => errorEnvelope(503, "no_embedding_provider", "No embedding provider."));
    const consoleError = vi.spyOn(console, "error").mockImplementation(() => {});
    render(<ChatWindowWrapper />);

    await user.type(screen.getByRole("textbox", { name: /your question/i }), "Why did this fail?");
    await user.click(screen.getByRole("button", { name: /ask sentinel/i }));

    const errorArticle = await screen.findByRole("article", { name: /the request failed/i });
    expect(errorArticle).toHaveTextContent(/no embedding provider is configured/i);
    // Note: error code label changed to "code:" (without "error")
    expect(errorArticle).toHaveTextContent(/code: no_embedding_provider/);
    consoleError.mockRestore();
  });

  it("renders an unreachable-backend error state distinctly from backend errors", async () => {
    const user = userEvent.setup();
    stubFetch(() => {
      throw new TypeError("fetch failed");
    });
    render(<ChatWindowWrapper />);

    await user.type(screen.getByRole("textbox", { name: /your question/i }), "Is anyone there?");
    await user.click(screen.getByRole("button", { name: /ask sentinel/i }));

    const errorArticle = await screen.findByRole("article", { name: /the request failed/i });
    // Distinct from backend-envelope errors: this is the transport-level state.
    expect(errorArticle).toHaveTextContent(/could not reach the sentinel backend/i);
    expect(errorArticle).not.toHaveTextContent(/error code:/i);
  });
});

describe("persistence — save and restore", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("shows 'Saved on this device' after a completed answer", async () => {
    const user = userEvent.setup();
    const { store } = stubLocalStorage();
    stubFetch(() => jsonResponse(queryResponseFixture()));
    render(<ChatWindowWrapper />);

    await user.type(
      screen.getByRole("textbox", { name: /your question/i }),
      "What is AAPL revenue?",
    );
    await user.click(screen.getByRole("button", { name: /ask sentinel/i }));

    await screen.findByRole("article", { name: /answer from sentinel/i });

    await waitFor(() => {
      expect(screen.getByText(/saved on this device/i)).toBeInTheDocument();
    });
    expect(store[STORAGE_KEY]).toBeTruthy();
  });

  it("restores saved messages on mount", async () => {
    // Pre-populate storage with a completed exchange
    const savedMessages = [
      {
        id: "u1",
        role: "user",
        question: "Restored question",
        status: "complete",
        savedAt: new Date().toISOString(),
      },
      {
        id: "a1",
        role: "assistant",
        question: "Restored question",
        status: "complete",
        answer: "Restored answer [1].",
        citations: [],
        savedAt: new Date().toISOString(),
      },
    ];
    stubLocalStorage({
      [STORAGE_KEY]: JSON.stringify({
        version: SCHEMA_VERSION,
        savedAt: new Date().toISOString(),
        messages: savedMessages,
      }),
    });

    render(<ChatWindowWrapper />);

    await waitFor(() => {
      expect(screen.getByRole("article", { name: /your question/i })).toHaveTextContent(
        "Restored question",
      );
    });
    expect(screen.getByRole("article", { name: /answer from sentinel/i })).toBeInTheDocument();
  });

  it("clears conversation and removes saved messages", async () => {
    const user = userEvent.setup();
    const { store } = stubLocalStorage();
    stubFetch(() => jsonResponse(queryResponseFixture()));
    render(<ChatWindowWrapper />);

    // Post a question
    await user.type(screen.getByRole("textbox", { name: /your question/i }), "Clear me");
    await user.click(screen.getByRole("button", { name: /ask sentinel/i }));
    await screen.findByRole("article", { name: /answer from sentinel/i });

    // Click clear → confirm
    await user.click(screen.getByRole("button", { name: /clear conversation history/i }));
    await user.click(screen.getByRole("button", { name: /confirm clear/i }));

    await waitFor(() => {
      expect(screen.getByRole("heading", { name: /ask a research question/i })).toBeInTheDocument();
    });
    expect(store[STORAGE_KEY]).toBeUndefined();
  });
});
