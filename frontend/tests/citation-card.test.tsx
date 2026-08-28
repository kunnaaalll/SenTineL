import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { CitationCard, citationDate, sourceTypeLabel } from "@/components/CitationCard";
import { citationFixture } from "./helpers";
import type { Citation } from "@/lib/api";

function asCitation(overrides: Record<string, unknown> = {}): Citation {
  return { ...citationFixture(), ...overrides } as Citation;
}

describe("CitationCard", () => {
  it("shows title collapsed and reveals excerpt, metadata, and link when expanded", async () => {
    const user = userEvent.setup();
    const { rerender } = render(
      <CitationCard
        citation={asCitation()}
        index={0}
        expanded={false}
        onToggle={() =>
          rerender(<CitationCard citation={asCitation()} index={0} expanded onToggle={() => {}} />)
        }
      />,
    );

    expect(screen.getByRole("button", { name: /apple inc\. 10-k/i })).toHaveAttribute(
      "aria-expanded",
      "false",
    );
    expect(screen.queryByText(/total net sales were/i)).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /apple inc\. 10-k/i }));

    expect(screen.getByRole("region", { name: /apple inc\. 10-k/i })).toBeInTheDocument();
    expect(screen.getByText(/total net sales were \$391,035 million/i)).toBeInTheDocument();
    expect(screen.getByText(/SEC filing/)).toBeInTheDocument();
    expect(screen.getByText("2024-11-01")).toBeInTheDocument();
    expect(screen.getByText("91% match")).toBeInTheDocument();

    const link = screen.getByRole("link", { name: /view source document/i });
    expect(link).toHaveAttribute(
      "href",
      "https://www.sec.gov/Archives/edgar/data/320193/example.htm",
    );
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", "noopener noreferrer");
  });

  it("is fully keyboard operable — the toggle is a native button", async () => {
    const user = userEvent.setup();
    let toggled = false;
    render(
      <CitationCard
        citation={asCitation({ url: null })}
        index={2}
        expanded={false}
        onToggle={() => {
          toggled = true;
        }}
      />,
    );

    const toggle = screen.getByRole("button", { name: /untitled|apple inc/i });
    toggle.focus();
    expect(toggle).toHaveFocus();
    await user.keyboard("{Enter}");
    expect(toggled).toBe(true);
    await user.keyboard(" "); // Space also activates buttons
    expect(toggled).toBe(true);
  });

  it("handles sources without URLs, dates, or scores gracefully", async () => {
    const user = userEvent.setup();
    render(
      <CitationCard
        citation={asCitation({
          url: null,
          score: null,
          section: null,
          title: "Market note",
          source_id: "NEWS:MSFT:2025-01-04",
        })}
        index={0}
        expanded
        onToggle={() => {}}
      />,
    );

    expect(screen.getByText(/no public link for this source/i)).toBeInTheDocument();
    expect(screen.queryByText(/match$/)).not.toBeInTheDocument();
    expect(screen.getByText(/market news/i)).toBeInTheDocument();
    expect(screen.getByText("2025-01-04")).toBeInTheDocument();
  });

  it("maps source_id prefixes to human labels with a safe fallback", () => {
    expect(sourceTypeLabel(asCitation())).toBe("SEC filing");
    expect(sourceTypeLabel(asCitation({ source_id: "TRANSCRIPT:AAPL:Q4" }))).toBe("Earnings call");
    expect(sourceTypeLabel(asCitation({ source_id: "WEIRD:THING" }))).toBe("Source");
  });

  it("extracts the publication date from title or source id", () => {
    expect(citationDate(asCitation())).toBe("2024-11-01");
    expect(citationDate(asCitation({ title: "Filing", source_id: "SEC:X" }))).toBeNull();
  });
});
