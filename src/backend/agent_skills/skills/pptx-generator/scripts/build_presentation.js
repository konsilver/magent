#!/usr/bin/env node

const fs = require("fs");
const path = require("path");

function loadPptxGenJS() {
  try {
    return require("pptxgenjs");
  } catch (_) {}
  try {
    const root = require("child_process")
      .execSync("npm root -g", { stdio: ["ignore", "pipe", "ignore"] })
      .toString()
      .trim();
    return require(path.join(root, "pptxgenjs"));
  } catch (err) {
    console.error(JSON.stringify({
      status: "error",
      error: `pptxgenjs not found: ${String(err)}`,
      hint: "Install with: npm install -g pptxgenjs",
    }));
    process.exit(2);
  }
}

function normalizeHex(value, fallback) {
  const raw = String(value || fallback || "").replace(/^#/, "").trim();
  return /^[0-9A-Fa-f]{6}$/.test(raw) ? raw.toUpperCase() : fallback;
}

function parseArgs(argv) {
  const args = { out: null, input: null };
  for (let i = 0; i < argv.length; i += 1) {
    if (argv[i] === "--out" && argv[i + 1]) args.out = argv[++i];
    else if (argv[i] === "--input" && argv[i + 1]) args.input = argv[++i];
  }
  return args;
}

async function readStdin() {
  return await new Promise((resolve, reject) => {
    let data = "";
    process.stdin.setEncoding("utf8");
    process.stdin.on("data", chunk => { data += chunk; });
    process.stdin.on("end", () => resolve(data));
    process.stdin.on("error", reject);
  });
}

function addBadge(pres, slide, theme, index) {
  slide.addShape(pres.shapes.ROUNDED_RECTANGLE, {
    x: 9.08, y: 5.08, w: 0.62, h: 0.34,
    fill: { color: theme.accent },
    line: { color: theme.accent },
    rectRadius: 0.08,
  });
  slide.addText(String(index).padStart(2, "0"), {
    x: 9.08, y: 5.08, w: 0.62, h: 0.34,
    fontFace: "Arial",
    fontSize: 10,
    color: "FFFFFF",
    bold: true,
    align: "center",
    valign: "mid",
  });
}

function addBullets(slide, bullets, box, theme, level = 0) {
  const items = Array.isArray(bullets) ? bullets.filter(Boolean).map(String) : [];
  if (items.length === 0) return;
  slide.addText(
    items.map(text => ({
      text,
      options: { bullet: { indent: 18 + level * 10 } },
    })),
    {
      ...box,
      fontFace: "Arial",
      fontSize: 20,
      color: theme.secondary,
      breakLine: true,
      paraSpaceAfterPt: 10,
      margin: 0.05,
      valign: "top",
    },
  );
}

function addTitle(slide, title, theme, opts = {}) {
  slide.addText(String(title || ""), {
    x: opts.x ?? 0.72,
    y: opts.y ?? 0.42,
    w: opts.w ?? 8.2,
    h: opts.h ?? 0.7,
    fontFace: opts.fontFace ?? "Arial",
    fontSize: opts.fontSize ?? 26,
    color: opts.color ?? theme.primary,
    bold: opts.bold ?? true,
    align: opts.align ?? "left",
    valign: "mid",
    margin: 0,
  });
}

function renderCover(pres, slide, spec, theme) {
  slide.background = { color: theme.bg };
  slide.addShape(pres.shapes.RECTANGLE, {
    x: 0, y: 0, w: 10, h: 5.625,
    fill: { color: theme.bg },
    line: { color: theme.bg },
  });
  slide.addShape(pres.shapes.RECTANGLE, {
    x: 0.6, y: 0.62, w: 0.16, h: 3.9,
    fill: { color: theme.accent },
    line: { color: theme.accent },
  });
  slide.addText(String(spec.title || ""), {
    x: 1.05, y: 1.1, w: 7.9, h: 1.2,
    fontFace: "Arial",
    fontSize: 30,
    color: theme.primary,
    bold: true,
    fit: "shrink",
    valign: "mid",
  });
  if (spec.subtitle) {
    slide.addText(String(spec.subtitle), {
      x: 1.08, y: 2.45, w: 6.8, h: 0.55,
      fontFace: "Arial",
      fontSize: 16,
      color: theme.secondary,
      fit: "shrink",
      valign: "mid",
    });
  }
  if (spec.body || spec.author) {
    slide.addText(String(spec.body || spec.author), {
      x: 1.08, y: 4.52, w: 4.2, h: 0.32,
      fontFace: "Arial",
      fontSize: 10,
      color: theme.secondary,
    });
  }
}

function renderToc(pres, slide, spec, theme, index) {
  slide.background = { color: "FFFFFF" };
  addTitle(slide, spec.title || "目录", theme);
  slide.addShape(pres.shapes.LINE, {
    x: 0.72, y: 1.16, w: 8.35, h: 0,
    line: { color: theme.light, pt: 1.4 },
  });
  const items = Array.isArray(spec.items) ? spec.items : spec.bullets;
  addBullets(slide, items, { x: 1.0, y: 1.55, w: 7.8, h: 3.2 }, theme);
  addBadge(pres, slide, theme, index);
}

function renderSection(pres, slide, spec, theme, index) {
  slide.background = { color: theme.primary };
  slide.addShape(pres.shapes.RECTANGLE, {
    x: 0, y: 0, w: 10, h: 5.625,
    fill: { color: theme.primary },
    line: { color: theme.primary },
  });
  slide.addShape(pres.shapes.RECTANGLE, {
    x: 0.78, y: 1.15, w: 1.05, h: 0.12,
    fill: { color: theme.accent },
    line: { color: theme.accent },
  });
  slide.addText(String(spec.title || ""), {
    x: 0.78, y: 1.55, w: 8.3, h: 0.95,
    fontFace: "Arial",
    fontSize: 28,
    color: "FFFFFF",
    bold: true,
    fit: "shrink",
  });
  if (spec.subtitle) {
    slide.addText(String(spec.subtitle), {
      x: 0.8, y: 2.7, w: 7.4, h: 0.5,
      fontFace: "Arial",
      fontSize: 14,
      color: theme.light,
      fit: "shrink",
    });
  }
  addBadge(pres, slide, { ...theme, accent: theme.light }, index);
}

function renderContent(pres, slide, spec, theme, index) {
  slide.background = { color: "FFFFFF" };
  addTitle(slide, spec.title || "", theme);
  slide.addShape(pres.shapes.LINE, {
    x: 0.72, y: 1.18, w: 8.55, h: 0,
    line: { color: theme.light, pt: 1.1 },
  });

  if (Array.isArray(spec.leftBullets) || Array.isArray(spec.rightBullets)) {
    if (spec.leftTitle) {
      slide.addText(String(spec.leftTitle), {
        x: 0.78, y: 1.45, w: 3.8, h: 0.35,
        fontFace: "Arial", fontSize: 15, color: theme.primary, bold: true,
      });
    }
    if (spec.rightTitle) {
      slide.addText(String(spec.rightTitle), {
        x: 5.2, y: 1.45, w: 3.8, h: 0.35,
        fontFace: "Arial", fontSize: 15, color: theme.primary, bold: true,
      });
    }
    addBullets(slide, spec.leftBullets, { x: 0.8, y: 1.85, w: 3.9, h: 2.8 }, theme);
    addBullets(slide, spec.rightBullets, { x: 5.2, y: 1.85, w: 3.9, h: 2.8 }, theme);
  } else {
    addBullets(slide, spec.bullets, { x: 0.88, y: 1.55, w: 8.25, h: 2.75 }, theme);
  }

  if (spec.body) {
    slide.addText(String(spec.body), {
      x: 0.9, y: 4.32, w: 8.15, h: 0.7,
      fontFace: "Arial",
      fontSize: 12,
      color: theme.secondary,
      margin: 0,
      fit: "shrink",
    });
  }

  const highlights = Array.isArray(spec.highlights) ? spec.highlights.slice(0, 3) : [];
  highlights.forEach((item, idx) => {
    const x = 0.88 + idx * 2.95;
    slide.addShape(pres.shapes.ROUNDED_RECTANGLE, {
      x, y: 4.45, w: 2.55, h: 0.62,
      fill: { color: idx % 2 === 0 ? theme.bg : theme.light },
      line: { color: theme.light, pt: 0.8 },
      radius: 0.08,
    });
    slide.addText(String(item), {
      x: x + 0.12, y: 4.58, w: 2.3, h: 0.26,
      fontFace: "Arial",
      fontSize: 10,
      color: theme.primary,
      bold: true,
      fit: "shrink",
      align: "center",
    });
  });

  addBadge(pres, slide, theme, index);
}

function renderSummary(pres, slide, spec, theme, index) {
  slide.background = { color: theme.bg };
  addTitle(slide, spec.title || "总结", theme, { y: 0.52 });
  slide.addShape(pres.shapes.RECTANGLE, {
    x: 0.76, y: 1.3, w: 8.48, h: 3.45,
    fill: { color: "FFFFFF" },
    line: { color: theme.light, pt: 1 },
    radius: 0.06,
  });
  addBullets(slide, spec.bullets, { x: 1.05, y: 1.65, w: 7.9, h: 2.45 }, theme);
  if (spec.body) {
    slide.addText(String(spec.body), {
      x: 1.06, y: 4.2, w: 7.8, h: 0.32,
      fontFace: "Arial",
      fontSize: 11,
      color: theme.secondary,
      fit: "shrink",
    });
  }
  addBadge(pres, slide, theme, index);
}

function renderSlides(pres, slides, theme) {
  slides.forEach((spec, idx) => {
    const slide = pres.addSlide();
    const type = String(spec.type || "content").toLowerCase();
    const pageIndex = idx + 1;
    if (type === "cover") renderCover(pres, slide, spec, theme);
    else if (type === "toc") renderToc(pres, slide, spec, theme, pageIndex);
    else if (type === "section") renderSection(pres, slide, spec, theme, pageIndex);
    else if (type === "summary") renderSummary(pres, slide, spec, theme, pageIndex);
    else renderContent(pres, slide, spec, theme, pageIndex);
  });
}

async function loadSpec() {
  const args = parseArgs(process.argv.slice(2));
  if (args.input) {
    return { spec: JSON.parse(fs.readFileSync(args.input, "utf8")), outOverride: args.out };
  }
  const raw = (await readStdin()).trim();
  if (!raw) {
    throw new Error("No JSON payload provided on stdin");
  }
  return { spec: JSON.parse(raw), outOverride: args.out };
}

async function main() {
  const PptxGenJS = loadPptxGenJS();
  const { spec, outOverride } = await loadSpec();
  if (!Array.isArray(spec.slides) || spec.slides.length === 0) {
    throw new Error("slides must be a non-empty array");
  }

  const theme = {
    primary: normalizeHex(spec.theme && spec.theme.primary, "1F2A44"),
    secondary: normalizeHex(spec.theme && spec.theme.secondary, "4A5B7A"),
    accent: normalizeHex(spec.theme && spec.theme.accent, "D97757"),
    light: normalizeHex(spec.theme && spec.theme.light, "E8D8CC"),
    bg: normalizeHex(spec.theme && spec.theme.bg, "F7F2EC"),
  };

  const outFile = String(outOverride || spec.out || "presentation.pptx");
  const outDir = path.dirname(outFile);
  if (outDir && outDir !== ".") {
    fs.mkdirSync(outDir, { recursive: true });
  }

  const pres = new PptxGenJS();
  pres.layout = "LAYOUT_16x9";
  pres.author = String(spec.author || "Jingxin-Agent");
  pres.subject = String(spec.subject || spec.title || "Presentation");
  pres.title = String(spec.title || "Presentation");
  pres.company = "Jingxin-Agent";
  pres.lang = "zh-CN";

  renderSlides(pres, spec.slides, theme);
  await pres.writeFile({ fileName: outFile });

  const stat = fs.statSync(outFile);
  console.log(JSON.stringify({
    status: "ok",
    out: outFile,
    slides: spec.slides.length,
    size_kb: Math.round(stat.size / 1024),
    theme,
  }));
}

main().catch((err) => {
  console.error(JSON.stringify({
    status: "error",
    error: String(err && err.message ? err.message : err),
  }));
  process.exit(1);
});
