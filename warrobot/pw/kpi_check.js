const { chromium } = require("playwright");
(async () => {
  const b = await chromium.launch({ channel: "chrome", headless: true });
  const p = await b.newPage({ viewport: { width: 1400, height: 1200 } });
  await p.goto("http://localhost/", { waitUntil: "networkidle", timeout: 90000 });
  await p.waitForTimeout(1200);
  const kpis = await p.evaluate(() => {
    const out = [];
    document.querySelectorAll(".kpi").forEach((k) => {
      out.push((k.innerText || "").replace(/\s+/g, " ").trim());
    });
    const svg = document.querySelector("svg");
    return { kpis: out, chart: !!svg, chartPoints: svg ? svg.querySelectorAll("circle,path").length : 0 };
  });
  console.log(JSON.stringify(kpis, null, 1).slice(0, 1200));
  const shot = "C:\\Users\\Administrator\\Desktop\\DeepSeek DeskTop\\WarRobot\\state\\web_final.png";
  await p.screenshot({ path: shot });
  console.log("shot:", shot);
  await b.close();
})();
