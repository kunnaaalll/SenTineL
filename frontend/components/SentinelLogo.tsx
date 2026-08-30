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

const SIZE_MAP: Record<string, number> = {
  sm: 20,
  md: 28,
  lg: 36,
  xl: 48,
};

/**
 * SentinelLogo — Code-native accessible flat geometric SVG branding for Sentinel.
 *
 * Motif: The Geometric S-Sentinel Mark.
 * Crisp, flat, architectural precision combining an interlocking geometric 'S'
 * structure with calibrated sentinel alignment nodes.
 *
 * Prohibited: Glowing balls, spheres, orbs, constellations, blobs, neon, glassmorphism.
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

  const getColors = () => {
    switch (theme) {
      case "monochrome":
        return {
          primary: "currentColor",
          accent: "currentColor",
          muted: "currentColor",
        };
      case "light":
        return {
          primary: "#1A1816",
          accent: "#C25E3E",
          muted: "#7C756D",
        };
      case "dark":
        return {
          primary: "#F5F2ED",
          accent: "#D97352",
          muted: "#A8A096",
        };
      case "default":
      default:
        return {
          primary: "var(--ink, #1A1816)",
          accent: "var(--accent, #C25E3E)",
          muted: "var(--ink-faint, #7C756D)",
        };
    }
  };

  const colors = getColors();

  // 1. Flat Geometric Symbol Mark (Used for symbol, compact, and lockups)
  const renderSymbol = (overrideSize = pixelSize) => (
    <svg
      width={overrideSize}
      height={overrideSize}
      viewBox="0 0 32 32"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      role="img"
      aria-label="Sentinel Logo"
      className={`shrink-0 ${className}`}
      {...svgProps}
    >
      <title>Sentinel Symbol</title>
      {/* Upper geometric S-arm (Terracotta) */}
      <path
        d="M26 8.5H12C9.51472 8.5 7.5 10.5147 7.5 13C7.5 15.4853 9.51472 17.5 12 17.5H20C22.4853 17.5 24.5 19.5147 24.5 22C24.5 24.4853 22.4853 26.5 20 26.5H6"
        stroke={colors.accent}
        strokeWidth="3.2"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      {/* Precision architectural sentinel diagonal cross-anchor (Charcoal/Primary) */}
      <path
        d="M10 5.5L22 26.5"
        stroke={colors.primary}
        strokeWidth="2.2"
        strokeLinecap="round"
        strokeOpacity="0.8"
      />
      {/* Top and bottom calibration sentinel nodes */}
      <circle cx="26" cy="8.5" r="2" fill={colors.accent} />
      <circle cx="6" cy="26.5" r="2" fill={colors.accent} />
      {/* Central focus node */}
      <circle cx="16" cy="16" r="1.75" fill={colors.primary} />
    </svg>
  );

  // Symbol only
  if (variant === "symbol" || variant === "compact") {
    return renderSymbol();
  }

  // Wordmark only
  if (variant === "wordmark") {
    return (
      <span
        className={`inline-flex items-baseline gap-1 font-display font-bold tracking-[0.05em] text-ink ${className}`}
      >
        <span style={{ color: colors.primary }}>SENTINEL</span>
        <span style={{ color: colors.accent }}>.</span>
      </span>
    );
  }

  // Full Lockup (Symbol + Wordmark + Optional Subtitle)
  return (
    <div className={`inline-flex items-center gap-2.5 select-none ${className}`}>
      {renderSymbol()}
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
