import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";

const here = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(here, "..");
const fixture = path.join(root, "fixtures", "benchmark_deck.json");

test("benchmark fixture is a six-slide Arabic/French deck", () => {
  const spec = JSON.parse(fs.readFileSync(fixture, "utf8"));
  assert.equal(spec.slides.length, 6);
  assert.equal(spec.rtl, true);
  assert.equal(spec.citations.length, 2);
  assert.ok(spec.slides.some((slide) => slide.kind === "chart"));
  assert.ok(spec.slides.some((slide) => slide.kind === "table"));
  assert.ok(spec.slides.some((slide) => slide.kind === "quote"));
  const all = JSON.stringify(spec);
  assert.match(all, /[\u0600-\u06ff]/, "Arabic text present");
  assert.match(all, /mandat présidentiel/, "French text present");
});

test("PptxGenJS generator writes a real PPTX package", () => {
  const outDir = fs.mkdtempSync(path.join(os.tmpdir(), "mafhum-slides-"));
  const output = path.join(outDir, "benchmark.pptx");
  execFileSync(process.execPath, [path.join(root, "generate.mjs"), fixture, output], {
    stdio: "pipe",
  });
  const bytes = fs.readFileSync(output);
  assert.ok(bytes.length > 10_000, `deck too small: ${bytes.length}`);
  assert.equal(bytes.subarray(0, 2).toString("ascii"), "PK", "must be a ZIP/OOXML package");
  fs.rmSync(outDir, { recursive: true, force: true });
});
