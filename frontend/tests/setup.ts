import { afterEach, beforeAll } from "vitest";
import { cleanup } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";

// RTL's auto-cleanup relies on a global afterEach, which doesn't exist when
// vitest globals are disabled (we import explicitly). Register it manually.
afterEach(() => {
  cleanup();
  try {
    if (typeof window !== "undefined" && typeof window.localStorage?.clear === "function") {
      window.localStorage.clear();
    }
  } catch {
    // Ignore when localStorage is mocked or restricted in tests
  }
});

// jsdom does not implement layout APIs the components touch.
beforeAll(() => {
  Element.prototype.scrollIntoView = () => {};
});
