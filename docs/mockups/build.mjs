// Renders the README mock-ups in this folder to images in /images (JPEG where photos make PNG heavy),
// and the brand images (images/brand/*.svg) to custom_components/singapore/brand/.
//
//   cd docs/mockups && npm install && node build.mjs [page.html ... | brand]
//
// With page names, only those pages are rendered; "brand" renders the brand images alone.
//
// Pages use <i m="mdiName"></i> placeholders, which are swapped for Material Design
// Icons (the icon set Home Assistant uses) before rendering.
import { readFileSync, writeFileSync, rmSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import * as mdi from "@mdi/js";
import { chromium } from "playwright";

const here = dirname(fileURLToPath(import.meta.url));
const root = join(here, "..", "..");
const out = join(root, "images");
const brandOut = join(root, "custom_components", "singapore", "brand");

// Home Assistant's brand sizes: a square icon at 256 and 512, logos 128 and 256 high, trimmed to
// their drawing on a transparent background; dark_ logos are for its dark theme.
const brand = [
  { src: "icon.svg", out: "icon.png", height: 256 },
  { src: "icon.svg", out: "icon@2x.png", height: 512 },
  { src: "logo-light.svg", out: "logo.png", height: 128 },
  { src: "logo-light.svg", out: "logo@2x.png", height: 256 },
  { src: "logo-dark.svg", out: "dark_logo.png", height: 128 },
  { src: "logo-dark.svg", out: "dark_logo@2x.png", height: 256 },
];

const pages = [
  { src: "hero.html", out: "hero.jpg", width: 1280 },
  { src: "alerts.html", out: "alerts.png", width: 1200 },
  { src: "cameras.html", out: "checkpoint-cameras.jpg", width: 900 },
  { src: "radar.html", out: "rain-radar.jpg", width: 600 },
  { src: "social.html", out: "social-preview.png", width: 1280, height: 640, scale: 1 },
];

const icon = (name) => {
  const path = mdi[name];
  if (!path) throw new Error(`Unknown icon ${name}`);
  return `<svg class="mdi" viewBox="0 0 24 24"><path d="${path}"/></svg>`;
};

const only = process.argv.slice(2);
const browser = await chromium.launch();
if (!only.length || only.includes("brand")) {
  for (const b of brand) {
    const page = await browser.newPage({ viewport: { width: 2000, height: 600 } });
    await page.setContent(
      `<body style="margin:0;background:transparent">${readFileSync(join(out, "brand", b.src), "utf8")}</body>`,
    );
    await page.evaluate(() => document.fonts.ready);
    // Fit the viewBox to what's drawn, so the PNG is trimmed to it at exactly the asked height.
    await page.evaluate((height) => {
      const svg = document.querySelector("svg");
      const box = svg.getBBox();
      svg.setAttribute("viewBox", `${box.x} ${box.y} ${box.width} ${box.height}`);
      svg.setAttribute("height", height);
      svg.setAttribute("width", Math.round((box.width / box.height) * height));
      svg.style.display = "block";
    }, b.height);
    await page.locator("svg").screenshot({ path: join(brandOut, b.out), omitBackground: true });
    await page.close();
    console.log(`custom_components/singapore/brand/${b.out}`);
  }
}
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
