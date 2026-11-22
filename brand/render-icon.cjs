// Renders brand/assets/icon.svg to the 1024x1024 icon.png electron-builder turns into .ico/.icns.
// Needs Playwright with Chromium (`npx playwright install chromium`); run it only when icon.svg changes.
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');

(async () => {
  const svg = fs.readFileSync(path.join(__dirname, 'assets', 'icon.svg'), 'utf8');
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1024, height: 1024 }, deviceScaleFactor: 1 });
  await page.setContent(`<body style="margin:0;background:transparent">${svg}</body>`);
  await page.screenshot({ path: path.join(__dirname, 'assets', 'icon.png'), omitBackground: true });
  await browser.close();
})();
