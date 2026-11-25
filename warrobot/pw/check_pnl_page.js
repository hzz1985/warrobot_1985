const { chromium } = require("playwright");
(async () => {
  const b = await chromium.launch({ channel: "chrome", headless: true });
  const p = await b.newPage({ viewport: { width: 1400, height: 1000 } });
  await p.goto("http://127.0.0.1/", { waitUntil: "networkidle", timeout: 90000 });
  await p.waitForTimeout(1000);
  const info = await p.evaluate(() => {
    const rows = [];
    document.querySelectorAll("tr").forEach((tr) => {
      const t = (tr.innerText || "").replace(/\s+/g, " ").trim();
      if (t.indexOf("آردینه") >= 0 || t.indexOf("آواپارس") >= 0) rows.push(t.slice(0, 200));
    });
    return { rows };
  });
  console.log(JSON.stringify(info, null, 1).slice(0, 1500));
  await b.close();
})();
