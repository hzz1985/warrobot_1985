const { chromium } = require("playwright");
(async () => {
  const b = await chromium.launch({ channel: "chrome", headless: true });
  const p = await b.newPage({ viewport: { width: 1360, height: 950 } });
  await p.goto("http://127.0.0.1:5077/", { waitUntil: "networkidle", timeout: 90000 });
  await p.waitForTimeout(800);
  const info = await p.evaluate(() => {
    const g = (sel) => {
      const e = document.querySelector(sel);
      if (!e) return null;
      const cs = getComputedStyle(e);
      return { color: cs.color, bg: cs.backgroundColor, text: (e.innerText || "").trim().slice(0, 40) };
    };
    return {
      body: g("body"),
      table: g("table"),
      th: g("th"),
      td: g("td"),
      h2: g("h2"),
      countTr: document.querySelectorAll("tr").length,
      countTab: document.querySelectorAll(".tab").length,
      tableHTML: (document.querySelector("table")?.innerHTML || "").slice(0, 300),
    };
  });
  console.log(JSON.stringify(info, null, 1).slice(0, 1600));
  await b.close();
})();
