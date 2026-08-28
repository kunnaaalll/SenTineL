"use client";

import { Children, cloneElement, isValidElement, type ReactNode } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import type { Citation } from "@/lib/api";

/**
 * Markdown-safe answer rendering.
 *
 * - react-markdown escapes raw HTML by default, so model output can never
 *   inject markup; GFM adds the tables comparison answers rely on.
 * - Inline `[n]` citation markers become accessible buttons that expand the
 *   matching source card. Markers outside the citation list render literally
 *   rather than silently vanishing.
 * - A trailing "Limitations:" section is split out and rendered as an
 *   explicit caveat panel instead of blending into the answer body.
 */

const CITATION_MARKER = /\[(\d{1,2})\]/g;

/**
 * Splits a trailing "Limitations:" section off the answer body. The heading
 * must sit at a line boundary followed by a colon — the exact shape the
 * backend's degradation ladder emits — so prose that merely mentions the
 * word is never mis-split.
 */
export function splitLimitations(answer: string): { main: string; limitations: string | null } {
  const match = /(?:^|\n)[ \t]*(limitations?:[ \t]*)/i.exec(answer);
  if (!match || !match[1]) {
    return { main: answer, limitations: null };
  }
  const contentBefore = answer.slice(0, match.index).trim();
  // A tiny fragment before the heading means this is not a trailing caveat
  // section (e.g. an answer that opens with a short clause) — leave it alone.
  if (contentBefore.length > 0 && contentBefore.length < 12) {
    return { main: answer, limitations: null };
  }
  return {
    main: answer.slice(0, match.index),
    limitations: answer.slice(match.index + match[0].length),
  };
}

const INSUFFICIENT_EVIDENCE_RE =
  /insufficient evidence|no relevant (documents|evidence|filings|sources)|(could|could not|can't|cannot) (find|locate|verify)|no (supporting|retrieved) (documents|evidence)/i;

/**
 * Heuristic mirror of the backend's explicit-refusal guarantee: an answer
 * with zero citations whose text says it could not ground the claim.
 */
export function isInsufficientEvidence(answer: string, citationCount: number): boolean {
  return citationCount === 0 && INSUFFICIENT_EVIDENCE_RE.test(answer);
}

function mapTextNodes(node: ReactNode, transform: (text: string) => ReactNode): ReactNode {
  return Children.map(node, (child) => {
    if (typeof child === "string") return transform(child);
    if (isValidElement<{ children?: ReactNode }>(child) && child.props.children !== undefined) {
      return cloneElement(child, undefined, mapTextNodes(child.props.children, transform));
    }
    return child;
  });
}

interface AnswerMarkdownProps {
  text: string;
  citations: Citation[];
  onMarkerClick: (citationIndex: number) => void;
}

export function AnswerMarkdown({ text, citations, onMarkerClick }: AnswerMarkdownProps) {
  const renderSegments = (value: string): ReactNode => {
    const parts: ReactNode[] = [];
    let lastIndex = 0;
    for (const match of value.matchAll(CITATION_MARKER)) {
      const markerIndex = match.index ?? 0;
      const number = Number.parseInt(match[1] ?? "", 10);
      parts.push(value.slice(lastIndex, markerIndex));
      if (number >= 1 && number <= citations.length) {
        const citation = citations[number - 1];
        if (!citation) {
          parts.push(match[0]);
          lastIndex = markerIndex + match[0].length;
          continue;
        }
        parts.push(
          <button
            key={`cite-${markerIndex}`}
            type="button"
            onClick={() => onMarkerClick(number - 1)}
            title={citation.title}
            className="mx-0.5 inline-flex h-5 min-w-5 translate-y-[-3px] cursor-pointer items-center justify-center rounded border border-line bg-accent-soft px-1 align-baseline font-mono text-[11px] font-semibold leading-none text-accent transition-enabled hover:bg-accent hover:text-on-accent"
          >
            {number}
            <span className="sr-only">
              , show source {number}: {citation.title}
            </span>
          </button>,
        );
      } else {
        // Out-of-range markers stay visible so nothing the backend emitted
        // disappears silently (defensive; validated responses never hit this).
        parts.push(
          <span key={`cite-literal-${markerIndex}`} className="font-mono text-xs text-ink-faint">
            {match[0]}
          </span>,
        );
      }
      lastIndex = markerIndex + match[0].length;
    }
    parts.push(value.slice(lastIndex));
    return parts;
  };

  const withMarkers = (children: ReactNode): ReactNode => mapTextNodes(children, renderSegments);

  const components: Components = {
    p: ({ children }) => <p className="my-2 first:mt-0 last:mb-0">{withMarkers(children)}</p>,
    li: ({ children }) => <li className="my-1 pl-1">{withMarkers(children)}</li>,
    td: ({ children }) => (
      <td className="border border-line px-2 py-1.5 align-top text-sm">{withMarkers(children)}</td>
    ),
    th: ({ children }) => (
      <th
        scope="col"
        className="border border-line bg-surface-muted px-2 py-1.5 text-left text-sm font-semibold"
      >
        {withMarkers(children)}
      </th>
    ),
    table: ({ children }) => (
      <div className="my-3 overflow-x-auto" role="region" aria-label="Data table" tabIndex={0}>
        <table className="w-full border-collapse text-left">{children}</table>
      </div>
    ),
    a: ({ children, href }) => (
      <a
        href={href}
        target="_blank"
        rel="noopener noreferrer"
        className="font-medium text-accent underline decoration-accent/40 underline-offset-2 transition-enabled hover:text-accent-strong"
      >
        {children}
      </a>
    ),
    ul: ({ children }) => (
      <ul className="my-2 list-disc space-y-0 pl-5 first:mt-0 last:mb-0">{children}</ul>
    ),
    ol: ({ children }) => (
      <ol className="my-2 list-decimal space-y-0 pl-5 first:mt-0 last:mb-0">{children}</ol>
    ),
    strong: ({ children }) => <strong className="font-semibold text-ink">{children}</strong>,
    h1: ({ children }) => (
      <h1 className="mt-3 mb-2 text-base font-semibold first:mt-0">{children}</h1>
    ),
    h2: ({ children }) => (
      <h2 className="mt-3 mb-2 text-base font-semibold first:mt-0">{children}</h2>
    ),
    h3: ({ children }) => (
      <h3 className="mt-3 mb-2 text-sm font-semibold first:mt-0">{children}</h3>
    ),
    h4: ({ children }) => (
      <h4 className="mt-3 mb-2 text-sm font-semibold first:mt-0">{children}</h4>
    ),
  };

  return (
    <div className="text-sm leading-relaxed text-ink-soft [&_code]:rounded [&_code]:bg-surface-muted [&_code]:px-1 [&_code]:py-0.5 [&_code]:font-mono [&_code]:text-[0.8em] [&_blockquote]:border-l-2 [&_blockquote]:border-line-strong [&_blockquote]:pl-3 [&_blockquote]:italic [&_blockquote]:text-ink-faint">
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>
        {text}
      </ReactMarkdown>
    </div>
  );
}
