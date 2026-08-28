import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { AgentTraceViewer, stepLabel } from "@/components/AgentTraceViewer";

describe("AgentTraceViewer", () => {
  it("renders nothing for an empty path", () => {
    const { container } = render(<AgentTraceViewer agentPath={[]} traceUrl={null} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("lists the simple-path steps in order with friendly labels", async () => {
    const user = userEvent.setup();
    render(
      <AgentTraceViewer
        agentPath={["classify", "rewrite", "embed", "retrieve", "generate"]}
        traceUrl={null}
      />,
    );

    await user.click(screen.getByText(/pipeline · 5 steps/i));
    const items = screen.getAllByRole("listitem");
    expect(items).toHaveLength(5);
    expect(items[0]).toHaveTextContent("Classify");
    expect(items[1]).toHaveTextContent("Rewrite query");
    expect(items[4]).toHaveTextContent("Generate answer");
  });

  it("shows the multi-hop path including compare only when it ran", async () => {
    const user = userEvent.setup();
    const { unmount } = render(
      <AgentTraceViewer
        agentPath={["classify", "fetch", "extract", "compare", "synthesize"]}
        traceUrl="https://cloud.langfuse.com/trace/abc123"
      />,
    );

    await user.click(screen.getByText(/pipeline · 5 steps/i));
    expect(screen.getByText("Compare entities")).toBeInTheDocument();

    const trace = screen.getByRole("link", { name: /view full trace/i });
    expect(trace).toHaveAttribute("href", "https://cloud.langfuse.com/trace/abc123");
    expect(trace).toHaveAttribute("target", "_blank");
    expect(trace).toHaveAttribute("rel", "noopener noreferrer");
    unmount();

    // compare skipped (single entity/period) — synthesize follows extract.
    render(
      <AgentTraceViewer
        agentPath={["classify", "fetch", "extract", "synthesize"]}
        traceUrl={null}
      />,
    );
    await user.click(screen.getByText(/pipeline · 4 steps/i));
    expect(screen.queryByText("Compare entities")).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /view full trace/i })).not.toBeInTheDocument();
  });

  it("falls back to the raw step name for unknown future steps", async () => {
    const user = userEvent.setup();
    render(<AgentTraceViewer agentPath={["classify", "quantize"]} traceUrl={null} />);
    await user.click(screen.getByText(/pipeline · 2 steps/i));
    expect(screen.getByText("quantize")).toBeInTheDocument();
  });

  it("labels every known step", () => {
    expect(stepLabel("fetch")).toBe("Gather sources");
    expect(stepLabel("synthesize")).toBe("Synthesize answer");
    expect(stepLabel("mystery")).toBe("mystery");
  });
});
