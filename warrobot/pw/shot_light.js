const { chromium } = require("playwright");
const path = require("path");
(async () => {
  const out = "C:\\Users\\Administrator\\Desktop\\DeepSeek DeskTop\\WarRobot\\state\\web_light.png";
  const b = await chromium.launch({ channel: "chrome", headless: true });
  const p = await b.newPage({ viewport: { width: 1360, height: 950 } });
  await p.goto("http://127.0.0.1:5077/", { waitUntil: "networkidle", timeout: 90000 });
  await p.waitForTimeout(1200);
  const tabs = await p.locator(".tab").allInnerTexts();
  console.log("tabs:", JSON.stringify(tabs));
  await p.screenshot({ path: out, fullPage: false });
  console.log("shot:", out);
  await b.close();
})();
