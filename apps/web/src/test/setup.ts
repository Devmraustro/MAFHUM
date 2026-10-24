/**
 * Vitest setup.
 *
 * Only environment gaps are filled here. Nothing about the components or the
 * API client is stubbed — those are the code under test.
 */
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

// @testing-library/react auto-registers cleanup when globals are enabled, but
// doing it explicitly keeps the ordering obvious.
afterEach(() => {
  cleanup();
});

/*
 * jsdom does not implement the <dialog> element's modal behaviour. The
 * polyfill below supplies the minimum the ConfirmDialog relies on: `open`,
 * `showModal()` and `close()`. It is installed only when the real methods are
 * absent, so it silently stops mattering once jsdom ships them.
 */
const proto = HTMLDialogElement.prototype as HTMLDialogElement & {
  showModal?: () => void;
};

if (typeof proto.showModal !== "function") {
  Object.defineProperty(proto, "showModal", {
    configurable: true,
    writable: true,
    value(this: HTMLDialogElement) {
      this.open = true;
      this.setAttribute("open", "");
      // Real showModal() fires no event; focus moves to the first focusable
      // element, which the autofocus attribute already handles for us.
    },
  });
}

if (typeof proto.close !== "function" || !("returnValue" in proto)) {
  Object.defineProperty(proto, "close", {
    configurable: true,
    writable: true,
    value(this: HTMLDialogElement) {
      this.open = false;
      this.removeAttribute("open");
      this.dispatchEvent(new Event("close"));
    },
  });
}
