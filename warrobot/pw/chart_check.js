const { chromium } = require("playwright");
(async () => {
  const b = await chromium.launch({ channel: "chrome", headless: true });
  const p = await b.newPage({ viewport: { width: 1400, height: 1300 } });
  await p.goto("http://localhost/", { waitUntil: "networkidle", timeout: 90000 });
  await p.waitForTimeout(1000);
  // open the sell tab (it holds the chart)
  const tabs = await p.locator(".tab").all();
  for (const t of tabs) {
    const txt = (await t.innerText()).trim();
    if (txt.indexOf("فروش") >= 0) { await t.click(); break; }
  }
  await p.waitForTimeout(1200);
  const info = await p.evaluate(() => {
    const lbl = document.querySelector(".point-label");
    const axis = document.querySelector(".axis");
    const cs = (el) => el ? getComputedStyle(el).fill : null;
    return { labelFill: cs(lbl), axisFill: cs(axis),
             labelText: lbl ? lbl.textContent : null,
             hasChart: !!document.querySelector("svg"),
             sellsRows: document.querySelectorAll("table")[2] ?
               document.querySelectorAll("table")[2].innerText.slice(0, 200) : "" };
  });
  console.log(JSON.stringify(info, null, 1).slice(0, 900));
  await p.screenshot({ path: "C:\\Users\\Administrator\\Desktop\\DeepSeek DeskTop\\WarRobot\\state\\web_chart.png" });
  await b.close();
})();
