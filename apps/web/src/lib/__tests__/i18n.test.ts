import { existsSync, readdirSync, readFileSync, statSync } from "node:fs";
import { join, resolve } from "node:path";
import { describe, expect, it } from "vitest";

import { DEFAULT_LOCALE, directionOf, formatBytes, t } from "@/lib/i18n";
import type { Locale } from "@/lib/i18n";

/**
 * The dictionaries are the product's single source of interface text. These
 * tests fail when the two locales drift, or when a component asks for a key
 * nobody defined — because the fallback renders the raw key to the user.
 */

// Vitest runs with the project root as cwd; resolving from there avoids
// depending on import.meta.url, which is not a file: URL in every environment.
const SRC_DIR = resolve(process.cwd(), "src");
const I18N_PATH = join(SRC_DIR, "lib", "i18n.ts");

it("resolves the source tree it is about to scan", () => {
  expect(existsSync(SRC_DIR), SRC_DIR).toBe(true);
  expect(existsSync(I18N_PATH), I18N_PATH).toBe(true);
});

function* walk(dir: string): Generator<string> {
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) {
      if (entry === "node_modules" || entry === "__tests__") continue;
      yield* walk(full);
    } else if (/\.(ts|tsx)$/.test(entry) && !entry.endsWith(".test.ts")) {
      yield full;
    }
  }
}

/** Static `t("literal.key")` references found across the app source. */
function referencedKeys(): Set<string> {
  const keys = new Set<string>();
  const pattern = /\bt\(\s*"([^"]+)"\s*\)/g;
  for (const file of walk(SRC_DIR)) {
    const source = readFileSync(file, "utf8");
    for (const match of source.matchAll(pattern)) {
      const key = match[1];
      if (key) keys.add(key);
    }
  }
  return keys;
}

function keysOf(locale: Locale): Set<string> {
  // Reading through `t` would hide a missing key behind the fallback, so the
  // dictionaries are read directly from the module source instead.
  const source = readFileSync(I18N_PATH, "utf8");
  const start = source.indexOf(`const ${locale}: Dictionary = {`);
  expect(start, `dictionary "${locale}" not found`).toBeGreaterThan(-1);
  const end = source.indexOf("\n};", start);
  const block = source.slice(start, end);
  const found = new Set<string>();
  for (const match of block.matchAll(/^\s*"([^"]+)":/gm)) {
    if (match[1]) found.add(match[1]);
  }
  return found;
}

describe("i18n", () => {
  const ar = keysOf("ar");
  const fr = keysOf("fr");

  it("Arabic is the default locale and is right-to-left", () => {
    expect(DEFAULT_LOCALE).toBe("ar");
    expect(directionOf("ar")).toBe("rtl");
    expect(directionOf("fr")).toBe("ltr");
  });

  it("defines the same non-trivial number of strings in both locales", () => {
    // 77 keys at the time of writing; the floor guards against a dictionary
    // being gutted, and the equality check is the real invariant.
    expect(ar.size).toBeGreaterThanOrEqual(70);
    expect(fr.size).toBe(ar.size);
  });

  it("keeps the Arabic and French key sets identical", () => {
    const missingInFrench = [...ar].filter((key) => !fr.has(key));
    const missingInArabic = [...fr].filter((key) => !ar.has(key));
    expect(missingInFrench).toEqual([]);
    expect(missingInArabic).toEqual([]);
  });

  it("has a real translation for every key (no empty values, no key echoed back)", () => {
    for (const locale of ["ar", "fr"] as const) {
      for (const key of keysOf(locale)) {
        const value = t(locale, key);
        expect(value, `${locale}:${key} is empty`).not.toBe("");
        expect(value, `${locale}:${key} fell back to the key name`).not.toBe(key);
      }
    }
  });

  it("defines every key that the application source references", () => {
    const referenced = referencedKeys();
    expect(referenced.size).toBeGreaterThan(20);
    const undefinedInArabic = [...referenced].filter((key) => !ar.has(key));
    const undefinedInFrench = [...referenced].filter((key) => !fr.has(key));
    expect(undefinedInArabic).toEqual([]);
    expect(undefinedInFrench).toEqual([]);
  });

  it("covers every processing status the API can return", () => {
    for (const status of ["pending", "processing", "ready", "failed"]) {
      expect(ar.has(`status.${status}`), `status.${status}`).toBe(true);
      expect(fr.has(`status.${status}`), `status.${status}`).toBe(true);
      expect(t("ar", `status.${status}`)).not.toBe(`status.${status}`);
    }
  });

  it("translates the two locales differently where the scripts differ", () => {
    // A copy-paste that left French identical to Arabic would pass every other
    // check, so assert the actual values differ.
    expect(t("fr", "documents.title")).not.toBe(t("ar", "documents.title"));
    expect(t("fr", "documents.upload")).not.toBe(t("ar", "documents.upload"));
  });

  it("carries an Arabic explanation of why a scanned PDF yields no text", () => {
    const message = t("ar", "detail.scanned");
    expect(message).toContain("OCR");
    expect(message.length).toBeGreaterThan(60);
  });

  describe("formatBytes", () => {
    it("labels sub-kilobyte counts in bytes", () => {
      expect(formatBytes(0, "ar")).toContain("0");
      expect(formatBytes(512, "fr")).toContain("512");
    });

    it("switches to KB then MB without inventing precision", () => {
      expect(formatBytes(2048, "fr")).toContain("KB");
      expect(formatBytes(5 * 1024 * 1024, "fr")).toContain("MB");
    });
  });
});
