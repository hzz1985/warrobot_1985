const { chromium } = require("playwright");
(async () => {
  const b = await chromium.launch({ channel: "chrome", headless: true });
  const p = await b.newPage({ viewport: { width: 1400, height: 1200 } });
  await p.goto("http://localhost/", { waitUntil: "networkidle", timeout: 90000 });
  await p.waitForTimeout(1500);
  const info = await p.evaluate(() => {
    const tables = [];
    document.querySelectorAll("table").forEach((t, idx) => {
      const rows = [];
      t.querySelectorAll("tr").forEach((tr) => rows.push((tr.innerText || "").replace(/\s+/g, " ").trim().slice(0, 120)));
      tables.push({ idx, rows: rows.slice(0, 8) });
    });
    return tables;
  });
  console.log(JSON.stringify(info, null, 1).slice(0, 2600));
  await b.close();
})();
