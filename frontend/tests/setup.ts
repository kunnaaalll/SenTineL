import { afterEach, beforeAll } from "vitest";
import { cleanup } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";

// RTL's auto-cleanup relies on a global afterEach, which doesn't exist when
// vitest globals are disabled (we import explicitly). Register it manually.
afterEach(() => {
  cleanup();
});

// jsdom does not implement layout APIs the components touch.
beforeAll(() => {
  Element.prototype.scrollIntoView = () => {};
});
