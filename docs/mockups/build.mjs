// Renders the README mock-ups in this folder to images in /images (JPEG where photos make PNG heavy).
//
//   cd docs/mockups && npm install && node build.mjs [page.html ...]
//
// With page names, only those pages are rendered.
//
// Pages use <i m="mdiName"></i> placeholders, which are swapped for Material Design
// Icons (the icon set Home Assistant uses) before rendering.
import { readFileSync, writeFileSync, rmSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import * as mdi from "@mdi/js";
import { chromium } from "playwright";

const here = dirname(fileURLToPath(import.meta.url));
const out = join(here, "..", "..", "images");

const pages = [
  { src: "hero.html", out: "hero.jpg", width: 1280 },
  { src: "alerts.html", out: "alerts.png", width: 1200 },
  { src: "cameras.html", out: "checkpoint-cameras.jpg", width: 900 },
  { src: "radar.html", out: "rain-radar.jpg", width: 1000 },
  { src: "social.html", out: "social-preview.png", width: 1280, height: 640, scale: 1 },
];

const icon = (name) => {
  const path = mdi[name];
  if (!path) throw new Error(`Unknown icon ${name}`);
  return `<svg class="mdi" viewBox="0 0 24 24"><path d="${path}"/></svg>`;
};

const only = process.argv.slice(2);
const browser = await chromium.launch();
for (const p of pages.filter((p) => !only.length || only.includes(p.src))) {
  const html = readFileSync(join(here, p.src), "utf8").replace(
    /<i m="(\w+)"><\/i>/g,
    (_, name) => icon(name),
  );
  const tmp = join(here, `.render-${p.src}`);
  writeFileSync(tmp, html);
  const page = await browser.newPage({
    viewport: { width: p.width, height: p.height ?? 100 },
    deviceScaleFactor: p.scale ?? 2,
    colorScheme: "dark",
  });
  await page.goto(`file://${tmp}`);
  await page.evaluate(() => document.fonts.ready);
  const jpeg = p.out.endsWith(".jpg");
  await page.screenshot({
    path: join(out, p.out),
    fullPage: !p.height,
    ...(jpeg && { type: "jpeg", quality: 85 }),
  });
  await page.close();
  rmSync(tmp);
  console.log(`images/${p.out}`);
}
await browser.close();
