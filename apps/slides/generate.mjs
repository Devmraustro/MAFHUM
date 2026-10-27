/**
 * PptxGenJS comparison generator for the MAFHUM Arabic/French benchmark.
 *
 * It consumes the SAME DeckSpec JSON as the production Python generator so the
 * comparison is about the library, not about different input. It is NOT the
 * production path: the Python API process has no Node runtime in its container,
 * and adding one would be an unverified deployment change. See docs/ADR/0005.
 *
 * Usage: node generate.mjs <spec.json> <output.pptx>
 */

import fs from "node:fs";
import path from "node:path";
import PptxGenJS from "pptxgenjs";

const [specPath, outputPath] = process.argv.slice(2);
if (!specPath || !outputPath) {
  console.error("usage: node generate.mjs <spec.json> <output.pptx>");
  process.exit(2);
}

const spec = JSON.parse(fs.readFileSync(specPath, "utf8"));
const pptx = new PptxGenJS();
pptx.defineLayout({ name: "MAFHUM", width: 13.333, height: 7.5 });
pptx.layout = "MAFHUM";
pptx.rtlMode = Boolean(spec.rtl);
pptx.theme = {
  headFontFace: "Arial",
  bodyFontFace: "Arial",
  lang: spec.rtl ? "ar-DZ" : "fr-FR",
};
pptx.author = "MAFHUM";
pptx.subject = "Editable Arabic/French benchmark deck (synthetic text)";
pptx.title = spec.title;

const NAVY = "0B2545";
const ACCENT = "1B6CA8";
const PALE = "F1F5FA";
const WHITE = "FFFFFF";
const INK = "141B2D";
const MUTED = "4A5A70";

const textOptions = (rtl, extra = {}) => ({
  fontFace: "Arial",
  lang: rtl ? "ar-DZ" : "fr-FR",
  rtlMode: Boolean(rtl),
  align: rtl ? "right" : "left",
  color: INK,
  ...extra,
});

function band(slide, title, number) {
  slide.addShape(pptx.ShapeType.rect, {
    x: 0, y: 0, w: "100%", h: 1.15,
    fill: { color: NAVY }, line: { color: NAVY },
  });
  slide.addText(title, textOptions(spec.rtl, {
    x: 0.55, y: 0.12, w: 12.2, h: 0.9, fontSize: 26, bold: true, color: WHITE,
  }));
  slide.addText(`${spec.draft_notice}  •  MAFHUM  •  ${number}`, textOptions(spec.rtl, {
    x: 0.55, y: 7.02, w: 12.2, h: 0.32, fontSize: 10, color: MUTED,
  }));
}

function citationFooter(slide, indices) {
  if (!indices.length) return;
  slide.addText(`[${indices.join("، ")}]`, textOptions(spec.rtl, {
    x: 0.55, y: 6.55, w: 12.2, h: 0.35, fontSize: 11, bold: true, color: ACCENT,
  }));
}

for (const [i, item] of spec.slides.entries()) {
  const number = i + 1;
  const slide = pptx.addSlide();
  if (item.kind !== "title") band(slide, item.title, number);

  if (item.kind === "title") {
    slide.addShape(pptx.ShapeType.rect, {
      x: 0, y: 0, w: "100%", h: "100%", fill: { color: NAVY }, line: { color: NAVY },
    });
    slide.addText(spec.title, textOptions(spec.rtl, {
      x: 0.8, y: 2.1, w: 11.7, h: 1.4, fontSize: 40, bold: true, color: WHITE,
    }));
    slide.addText(spec.subtitle, textOptions(spec.rtl, {
      x: 0.8, y: 3.7, w: 11.7, h: 0.8, fontSize: 20, color: PALE,
    }));
    slide.addText(spec.draft_notice, textOptions(spec.rtl, {
      x: 0.8, y: 5.6, w: 11.7, h: 0.6, fontSize: 14, color: PALE,
    }));
  } else if (item.kind === "bullets") {
    slide.addText(
      item.items.map((text) => ({ text: `• ${text}`, options: { breakLine: true } })),
      textOptions(spec.rtl, { x: 0.55, y: 1.45, w: 12.2, h: 4.6, fontSize: 18, valign: "mid" }),
    );
  } else if (item.kind === "quote") {
    slide.addShape(pptx.ShapeType.rect, {
      x: 0.55, y: 1.45, w: 12.2, h: 3.1, fill: { color: PALE }, line: { color: PALE },
    });
    slide.addText(item.text, textOptions(spec.rtl, {
      x: 0.7, y: 1.55, w: 11.9, h: 2.9, fontSize: 18, valign: "mid",
    }));
    slide.addText(item.items[0] ?? "", textOptions(spec.rtl, {
      x: 0.55, y: 4.85, w: 12.2, h: 0.7, fontSize: 13, color: MUTED,
    }));
  } else if (item.kind === "chart") {
    slide.addChart(
      pptx.ChartType.bar,
      [{ name: spec.rtl ? "الاقتباسات" : "Citations", labels: item.chart_labels, values: item.chart_values }],
      { x: 0.7, y: 1.45, w: 11.9, h: 4.5, showLegend: false, rtlMode: Boolean(spec.rtl) },
    );
  } else if (item.kind === "table") {
    const header = item.table_headers.map((text) => ({ text, options: textOptions(spec.rtl, { bold: true, color: WHITE, fill: { color: NAVY } }) }));
    const body = item.table_rows.map((row) =>
      row.map((text) => ({ text, options: textOptions(spec.rtl, { fontSize: 12 }) })),
    );
    slide.addTable([header, ...body], {
      x: 0.55, y: 1.45, w: 12.2, h: 4.4, border: { type: "solid", color: PALE, pt: 1 },
      rtlMode: Boolean(spec.rtl),
    });
  }
  citationFooter(slide, item.citation_indices ?? []);
}

fs.mkdirSync(path.dirname(path.resolve(outputPath)), { recursive: true });
await pptx.writeFile({ fileName: outputPath });
console.log(`generated ${outputPath} (${spec.slides.length} slides)`);
