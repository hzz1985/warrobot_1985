// Screenshot the dashboard so the design can be reviewed without opening it.
const { chromium } = require("playwright");
const path = require("path");

(async () => {
  const out = path.join("C:\\Users\\Administrator\\Desktop\\DeepSeek DeskTop\\WarRobot", "state", "webapp_shot.png");
  const browser = await chromium.launch({ channel: "chrome", headless: true });
  const page = await browser.newPage({ viewport: { width: 1360, height: 1000 }, deviceScaleFactor: 1 });
  await page.goto("http://127.0.0.1:5077/", { waitUntil: "networkidle", timeout: 90000 });
  await page.waitForTimeout(1500);
  await page.screenshot({ path: out, fullPage: false });
  const second = path.join("C:\\Users\\Administrator\\Desktop\\DeepSeek DeskTop\\WarRobot", "state", "webapp_shot_full.png");
  await page.screenshot({ path: second, fullPage: true });
  console.log("shots:", out, second);
  await browser.close();
})();
