import puppeteer from "puppeteer";

const BASE = "http://localhost:8787";
const DIR = new URL(".", import.meta.url).pathname;

// Find a good session for the detail page
const sessionsPage = await fetch(`${BASE}/sessions`);
const html = await sessionsPage.text();
const match = html.match(/href="\/sessions\/([^"]+)"/);
const sessionId = match ? match[1] : null;

const pages = [
  { name: "1-overview", path: "/", width: 1280, height: 850 },
  { name: "2-overview-full", path: "/", width: 1280, height: 1400 },
  { name: "3-projects", path: "/projects", width: 1280, height: 620 },
  { name: "4-sessions", path: "/sessions", width: 1280, height: 750 },
  ...(sessionId
    ? [{ name: "5-session-detail", path: `/sessions/${sessionId}`, width: 1280, height: 1100 }]
    : []),
  { name: "6-tools", path: "/tools", width: 1280, height: 1400 },
  { name: "7-insights", path: "/insights", width: 1280, height: 2200 },
];

const browser = await puppeteer.launch({
  headless: true,
  args: ["--no-sandbox", "--disable-setuid-sandbox"],
});

for (const p of pages) {
  const page = await browser.newPage();
  await page.setViewport({ width: p.width, height: p.height, deviceScaleFactor: 1 });
  await page.goto(`${BASE}${p.path}`, { waitUntil: "networkidle0" });
  // Wait for Chart.js animations to complete (default 1000ms)
  await new Promise((r) => setTimeout(r, 2000));
  await page.screenshot({ path: `${DIR}${p.name}.png`, fullPage: false });
  console.log(`${p.name}.png captured`);
  await page.close();
}

await browser.close();
console.log("Done.");
