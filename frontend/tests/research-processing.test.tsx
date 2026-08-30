/**
 * tests/research-processing.test.tsx
 *
 * Offline unit tests for ResearchProcessingState component.
 * Verifies the restrained research signal treatment, rotating stages,
 * accessibility live region, and absence of generic spinners or glowing orbs.
 */

import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, act } from "@testing-library/react";
import { ResearchProcessingState } from "@/components/ResearchProcessingState";

describe("ResearchProcessingState component", () => {
  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it("renders with the initial neutral research copy and accessible live region", () => {
    render(<ResearchProcessingState />);

    const liveRegion = screen.getByRole("status");
    expect(liveRegion).toBeInTheDocument();
    expect(liveRegion).toHaveAttribute("aria-live", "polite");
    expect(screen.getByText(/sentinel is researching/i)).toBeInTheDocument();
    expect(screen.getByText(/sec filings & news/i)).toBeInTheDocument();
    expect(screen.getByText(/citation verification/i)).toBeInTheDocument();
  });

  it("rotates through neutral research phases over time", () => {
    vi.useFakeTimers();
    render(<ResearchProcessingState />);

    expect(screen.getByText(/sentinel is researching/i)).toBeInTheDocument();

    // Advance 3.5 seconds to next phase
    act(() => {
      vi.advanceTimersByTime(3500);
    });
    expect(screen.getByText(/reviewing filings and market sources/i)).toBeInTheDocument();

    // Advance 3.5 seconds to next phase
    act(() => {
      vi.advanceTimersByTime(3500);
    });
    expect(screen.getByText(/cross-checking evidence/i)).toBeInTheDocument();

    // Advance 3.5 seconds to next phase
    act(() => {
      vi.advanceTimersByTime(3500);
    });
    expect(screen.getByText(/preparing a cited answer/i)).toBeInTheDocument();
  });

  it("renders backend-specific stage text when provided", () => {
    render(<ResearchProcessingState stage="Synthesizing multi-entity financial comparison" />);
    expect(screen.getByText("Synthesizing multi-entity financial comparison")).toBeInTheDocument();
  });

  it("displays multi-agent pipeline indicator when forcedAgents is true", () => {
    render(<ResearchProcessingState forcedAgents={true} />);
    expect(screen.getByText(/multi-agent synthesis pipeline/i)).toBeInTheDocument();
  });

  it("does not contain glowing orbs, bouncing balls, or fake percentages", () => {
    const { container } = render(<ResearchProcessingState />);
    const html = container.innerHTML;

    expect(html).not.toMatch(/orb-breathe/i);
    expect(html).not.toMatch(/constellation/i);
    expect(html).not.toMatch(/%\s*completed/i);
    expect(html).not.toMatch(/spinner/i);
  });
});
