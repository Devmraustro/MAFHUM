import { existsSync, statSync } from "node:fs";
import { join, resolve } from "node:path";

import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { HomeContent } from "@/app/home-content";
import { LocaleProvider } from "@/lib/locale-context";
import { t } from "@/lib/i18n";

/**
 * Landing page contract.
 *
 * The landing page is the one surface a visitor judges the product by, so these
 * tests pin the claims that must not drift: the hero call to action points at the
 * real registration route, the four capabilities are all present, the
 * limitations are stated on the page (not only in a document), and the imagery
 * carries text alternatives.
 */
function renderHome() {
  return render(
    <LocaleProvider>
      <HomeContent />
    </LocaleProvider>,
  );
}

describe("landing page", () => {
  it("leads with the product claim and a working registration link", () => {
    renderHome();
    // This project deliberately does not install jest-dom; plain assertions are
    // used so a matcher cannot hide a missing element.
    expect(screen.getByRole("heading", { level: 1 }).textContent).toBe(
      t("ar", "landing.heroTitle"),
    );
    const primary = screen.getAllByRole("link", { name: t("ar", "landing.heroCta") });
    // One in the hero, one in the closing band: both must reach registration.
    expect(primary.length).toBeGreaterThan(0);
    for (const link of primary) {
      expect(link.getAttribute("href")).toBe("/?mode=register");
    }
  });

  it("describes all four implemented capabilities", () => {
    renderHome();
    for (const key of [
      "landing.feature1Title",
      "landing.feature2Title",
      "landing.feature3Title",
      "landing.feature4Title",
    ]) {
      expect(screen.getByRole("heading", { name: t("ar", key) })).toBeTruthy();
    }
  });

  it("states the limitations on the page itself", () => {
    renderHome();
    // No OCR and no bundled engine are the two claims most likely to be
    // overstated elsewhere; the page must say them out loud.
    expect(screen.getByText(t("ar", "landing.statsNoOcr"))).toBeTruthy();
    expect(screen.getByText(t("ar", "landing.principle3"))).toBeTruthy();
  });

  it("gives every illustration a text alternative", () => {
    const { container } = renderHome();
    const images = container.querySelectorAll("img");
    expect(images.length).toBeGreaterThanOrEqual(4);
    for (const image of images) {
      expect(image.getAttribute("alt") ?? "").not.toBe("");
    }
  });

  it("does not promise an AI that writes content", () => {
    const { container } = renderHome();
    const text = container.textContent ?? "";
    // The product selects real passages; claiming generation would be false.
    expect(text).not.toMatch(/يكتب لك|يولّد لك|génère pour vous/i);
  });
});

/**
 * Asset existence is asserted on disk, not through the network: a renamed or
 * missing illustration in `public/landing/` would otherwise only show up as a
 * broken image in production. `next/image` cannot catch it, because the path is a
 * runtime string.
 */
describe("landing assets", () => {
  it("ships every referenced illustration", () => {
    const root = resolve(process.cwd(), "public", "landing");
    for (const file of [
      "hero-documents.webp",
      "grounded-research.webp",
      "study-flashcards.webp",
      "slides-tools.webp",
    ]) {
      const path = join(root, file);
      expect(existsSync(path), `missing ${path}`).toBe(true);
      // A zero-byte file would satisfy existence and break the page.
      expect(statSync(path).size).toBeGreaterThan(1024);
    }
  });
});
