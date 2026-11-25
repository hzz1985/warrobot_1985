// Screenshot each dashboard tab so the content can be reviewed.
const { chromium } = require("playwright");
const path = require("path");

const OUT = "C:\\Users\\Administrator\\Desktop\\DeepSeek DeskTop\\WarRobot\\state";

(async () => {
  const browser = await chromium.launch({ channel: "chrome", headless: true });
  const page = await browser.newPage({ viewport: { width: 1360, height: 1400 }, deviceScaleFactor: 1 });
  await page.goto("http://127.0.0.1:5077/", { waitUntil: "networkidle", timeout: 90000 });
  await page.waitForTimeout(1200);

  const tabs = await page.locator(".tab").all();
  const names = [];
  for (let i = 0; i < tabs.length; i++) {
    const t = (await tabs[i].innerText()).trim();
    names.push(t);
  }
  console.log("tabs:", JSON.stringify(names));

  for (let i = 0; i < Math.min(tabs.length, 3); i++) {
    await tabs[i].click();
    await page.waitForTimeout(900);
    const file = path.join(OUT, `tab_${i}.png`);
    await page.screenshot({ path: file, fullPage: false });
    console.log("shot", i, names[i], "->", file);
  }
  await browser.close();
})();
