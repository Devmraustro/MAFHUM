import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { dictionariesByLocale } from "@/lib/i18n";

const { ar, fr } = dictionariesByLocale;
import { LocaleProvider, useLocale } from "@/lib/locale-context";

/**
 * Bilingual behaviour, asserted rather than assumed.
 *
 * The interface must be Arabic-first and fully usable in French. Two failures are
 * possible and neither is visible in a passing build: a locale that changes the
 * strings but leaves the document in RTL, and a French dictionary that silently
 * lost keys. Both are covered here.
 */

function Probe() {
  const { locale, setLocale, t, dir } = useLocale();
  return (
    <div>
      <p data-testid="locale">{locale}</p>
      <p data-testid="dir">{dir}</p>
      <p data-testid="title">{t("landing.heroTitle")}</p>
      <button type="button" onClick={() => setLocale("fr")}>
        fr
      </button>
    </div>
  );
}

describe("locale switching", () => {
  it("starts in Arabic RTL and switches to French LTR", async () => {
    render(
      <LocaleProvider>
        <Probe />
      </LocaleProvider>,
    );

    expect(screen.getByTestId("locale").textContent).toBe("ar");
    expect(screen.getByTestId("dir").textContent).toBe("rtl");
    const arabicTitle = screen.getByTestId("title").textContent;

    screen.getByText("fr").click();

    await screen.findByTestId("locale");
    expect(screen.getByTestId("locale").textContent).toBe("fr");
    expect(screen.getByTestId("dir").textContent).toBe("ltr");

    // The visible string really changed, and the document itself followed: a page
    // with French text but `dir="rtl"` is broken for every reader.
    expect(screen.getByTestId("title").textContent).not.toBe(arabicTitle);
    expect(document.documentElement.lang).toBe("fr");
    expect(document.documentElement.dir).toBe("ltr");
  });

  it("keeps the two dictionaries in exact key parity", () => {
    expect(Object.keys(fr).sort()).toEqual(Object.keys(ar).sort());
    expect(Object.keys(ar).length).toBeGreaterThan(250);
  });

  it("has no empty translations in either language", () => {
    for (const [locale, dictionary] of [
      ["ar", ar],
      ["fr", fr],
    ] as const) {
      for (const [key, value] of Object.entries(dictionary)) {
        expect(value.trim(), `${locale}:${key} is empty`).not.toBe("");
      }
    }
  });

  it("does not leave Arabic copy inside the French dictionary", () => {
    // Product names and tokens such as "MAFHUM", "PDF", "PPTX" are expected. A
    // sentence in Arabic script is not: it means a key was copied, not translated.
    const arabicScript = /[\u0600-\u06FF]{3,}/;
    const offenders = Object.entries(fr)
      .filter(([, value]) => arabicScript.test(value))
      .map(([key]) => key);
    expect(offenders).toEqual([]);
  });
});
