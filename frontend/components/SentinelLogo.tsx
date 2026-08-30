"use client";

import React from "react";

export type LogoVariant = "symbol" | "wordmark" | "full" | "compact";
export type LogoTheme = "default" | "monochrome" | "light" | "dark";

export interface SentinelLogoProps extends React.SVGProps<SVGSVGElement> {
  variant?: LogoVariant;
  theme?: LogoTheme;
  size?: "sm" | "md" | "lg" | "xl" | number;
  className?: string;
  showSubtitle?: boolean;
}

const SIZE_MAP = {
  sm: 20,
  md: 28,
  lg: 36,
  xl: 48,
};

/**
 * SentinelBrandLogo — Code-native accessible SVG branding for Sentinel.
 *
 * Design Motif: "The Vigilant Aperture" — A precision optical radar signal
 * combining an inner focal point, cardinal calibration nodes, and dual
 * concentric scanning arcs representing continuous vigilance over financial filings.
 */
export function SentinelLogo({
  variant = "symbol",
  theme = "default",
  size = "md",
  className = "",
  showSubtitle = false,
  ...svgProps
}: SentinelLogoProps) {
  const pixelSize = typeof size === "number" ? size : (SIZE_MAP[size] ?? 28);

  // Resolve color tokens based on theme
  const getColors = () => {
    switch (theme) {
      case "monochrome":
        return {
          primary: "currentColor",
          accent: "currentColor",
          muted: "currentColor",
          background: "transparent",
        };
      case "light":
        return {
          primary: "#1A1816",
          accent: "#C25E3E",
          muted: "#7C756D",
          background: "#FAF7F2",
        };
      case "dark":
        return {
          primary: "#F5F2ED",
          accent: "#D97352",
          muted: "#A8A096",
          background: "#1E1C1A",
        };
      case "default":
      default:
        return {
          primary: "var(--ink, #1A1816)",
          accent: "var(--accent, #C25E3E)",
          muted: "var(--ink-faint, #7C756D)",
          background: "transparent",
        };
    }
  };

  const colors = getColors();

  // 1. Symbol Only (Icon)
  if (variant === "symbol" || variant === "compact") {
    return (
      <svg
        width={pixelSize}
        height={pixelSize}
        viewBox="0 0 32 32"
        fill="none"
        xmlns="http://www.w3.org/2000/svg"
        role="img"
        aria-label="Sentinel Logo"
        className={`shrink-0 transition-transform duration-200 ${className}`}
        {...svgProps}
      >
        <title>Sentinel Symbol</title>
        {/* Outer orbital lens sweep (top-left to bottom-right) */}
        <path
          d="M 16 3 C 23.18 3 29 8.82 29 16 C 29 19.3 27.77 22.31 25.75 24.6"
          stroke={colors.accent}
          strokeWidth="2"
          strokeLinecap="round"
          strokeDasharray="28 4"
        />
        {/* Counter-orbital sweep (bottom-right to top-left) */}
        <path
          d="M 16 29 C 8.82 29 3 23.18 3 16 C 3 12.7 4.23 9.69 6.25 7.4"
          stroke={colors.primary}
          strokeWidth="2"
          strokeLinecap="round"
          strokeOpacity="0.85"
        />
        {/* Precision aperture ring */}
        <circle
          cx="16"
          cy="16"
          r="7.5"
          stroke={colors.accent}
          strokeWidth="1.5"
          strokeDasharray="2 3"
          strokeOpacity="0.75"
        />
        {/* Cardinal calibration ticks */}
        <line
          x1="16"
          y1="5.5"
          x2="16"
          y2="7.5"
          stroke={colors.primary}
          strokeWidth="1.5"
          strokeLinecap="round"
        />
        <line
          x1="16"
          y1="24.5"
          x2="16"
          y2="26.5"
          stroke={colors.primary}
          strokeWidth="1.5"
          strokeLinecap="round"
        />
        <line
          x1="5.5"
          y1="16"
          x2="7.5"
          y2="16"
          stroke={colors.primary}
          strokeWidth="1.5"
          strokeLinecap="round"
        />
        <line
          x1="24.5"
          y1="16"
          x2="26.5"
          y2="16"
          stroke={colors.primary}
          strokeWidth="1.5"
          strokeLinecap="round"
        />
        {/* Core vigilant eye node */}
        <circle cx="16" cy="16" r="3" fill={colors.accent} />
        <circle
          cx="16"
          cy="16"
          r="1.2"
          fill={colors.background === "transparent" ? "#FFF" : colors.background}
        />
      </svg>
    );
  }

  // 2. Horizontal Wordmark Only
  if (variant === "wordmark") {
    return (
      <span
        className={`inline-flex items-baseline gap-1.5 font-display font-semibold tracking-tight ${className}`}
      >
        <span style={{ color: colors.primary }} className="text-lg tracking-[0.06em]">
          SENTINEL
        </span>
        <span style={{ color: colors.accent }} className="text-base font-bold">
          .
        </span>
      </span>
    );
  }

  // 3. Full Lockup (Symbol + Wordmark + Optional Subtitle)
  return (
    <div className={`inline-flex items-center gap-2.5 select-none ${className}`}>
      <svg
        width={pixelSize}
        height={pixelSize}
        viewBox="0 0 32 32"
        fill="none"
        xmlns="http://www.w3.org/2000/svg"
        role="img"
        aria-label="Sentinel Logo"
        className="shrink-0"
        {...svgProps}
      >
        <title>Sentinel Logo</title>
        <path
          d="M 16 3 C 23.18 3 29 8.82 29 16 C 29 19.3 27.77 22.31 25.75 24.6"
          stroke={colors.accent}
          strokeWidth="2"
          strokeLinecap="round"
          strokeDasharray="28 4"
        />
        <path
          d="M 16 29 C 8.82 29 3 23.18 3 16 C 3 12.7 4.23 9.69 6.25 7.4"
          stroke={colors.primary}
          strokeWidth="2"
          strokeLinecap="round"
          strokeOpacity="0.85"
        />
        <circle
          cx="16"
          cy="16"
          r="7.5"
          stroke={colors.accent}
          strokeWidth="1.5"
          strokeDasharray="2 3"
          strokeOpacity="0.75"
        />
        <line
          x1="16"
          y1="5.5"
          x2="16"
          y2="7.5"
          stroke={colors.primary}
          strokeWidth="1.5"
          strokeLinecap="round"
        />
        <line
          x1="16"
          y1="24.5"
          x2="16"
          y2="26.5"
          stroke={colors.primary}
          strokeWidth="1.5"
          strokeLinecap="round"
        />
        <line
          x1="5.5"
          y1="16"
          x2="7.5"
          y2="16"
          stroke={colors.primary}
          strokeWidth="1.5"
          strokeLinecap="round"
        />
        <line
          x1="24.5"
          y1="16"
          x2="26.5"
          y2="16"
          stroke={colors.primary}
          strokeWidth="1.5"
          strokeLinecap="round"
        />
        <circle cx="16" cy="16" r="3" fill={colors.accent} />
        <circle
          cx="16"
          cy="16"
          r="1.2"
          fill={colors.background === "transparent" ? "#FFF" : colors.background}
        />
      </svg>
      <div className="flex flex-col text-left">
        <span className="font-display text-base font-bold tracking-[0.06em] text-ink leading-tight">
          SENTINEL
        </span>
        {showSubtitle && (
          <span className="font-sans text-[10px] font-medium tracking-[0.14em] uppercase text-ink-faint leading-none mt-0.5">
            Financial Intelligence
          </span>
        )}
      </div>
    </div>
  );
}
