import { describe, expect, it } from "vitest";
import { DICTIONARIES, validateDictionary, getDictionary, type Locale } from "./dictionary";

describe("dictionary (ar/fr)", () => {
  it("has identical key structure across locales", () => {
    const arKeys = sortedKeys(DICTIONARIES.ar);
    const frKeys = sortedKeys(DICTIONARIES.fr);
    expect(frKeys).toEqual(arKeys);
  });

  it("has no empty strings or missing values in either locale", () => {
    for (const locale of ["ar", "fr"] as Locale[]) {
      const problems = validateDictionary(locale);
      expect(problems, `problems for ${locale}: ${problems.join(", ")}`).toHaveLength(0);
    }
  });

  it("returns RTL dir for Arabic and LTR for French", () => {
    expect(getDictionary("ar").dir).toBe("rtl");
    expect(getDictionary("fr").dir).toBe("ltr");
  });

  it("keeps MAFHUM brand name identical in both locales", () => {
    expect(DICTIONARIES.ar.site.name).toBe("MAFHUM");
    expect(DICTIONARIES.fr.site.name).toBe("MAFHUM");
  });
});

function sortedKeys(value: unknown, path = ""): string[] {
  const keys: string[] = [];
  const walk = (v: unknown, p: string): void => {
    if (v !== null && typeof v === "object" && !Array.isArray(v)) {
      for (const [k, child] of Object.entries(v as Record<string, unknown>)) {
        walk(child, `${p}${k}.`);
      }
    } else if (typeof v === "string") {
      keys.push(p.slice(0, -1));
    }
  };
  walk(value, path);
  return keys.sort();
}
