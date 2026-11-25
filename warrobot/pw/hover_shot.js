const { chromium } = require("playwright");
(async () => {
  const b = await chromium.launch({ channel: "chrome", headless: true });
  const p = await b.newPage({ viewport: { width: 1400, height: 900 } });
  await p.goto("http://localhost/", { waitUntil: "networkidle", timeout: 90000 });
  await p.waitForTimeout(800);
  // scroll to the positions table and hover a data row
  const row = p.locator("table tr").nth(1);
  await row.scrollIntoViewIfNeeded();
  await row.hover();
  await p.waitForTimeout(400);
  const shot = "C:\\Users\\Administrator\\Desktop\\DeepSeek DeskTop\\WarRobot\\state\\web_hover.png";
  await p.screenshot({ path: shot });
  console.log("shot:", shot);
  await b.close();
})();
