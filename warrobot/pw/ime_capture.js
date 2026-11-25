const { chromium } = require("playwright");
(async () => {
  const b = await chromium.launch({ channel: "chrome", headless: true });
  const p = await b.newPage({ viewport: { width: 1400, height: 900 } });
  const hits = [];
  p.on("request", (r) => {
    const u = r.url();
    if (u.includes("ime.co.ir") && (r.resourceType() === "xhr" || r.resourceType() === "fetch"))
      hits.push({ m: r.method(), u: u.slice(0, 160) });
  });
  const resp = [];
  p.on("response", async (r) => {
    const u = r.url();
    if (u.includes("ime.co.ir") && /api|json|price|report/i.test(u))
      resp.push({ s: r.status(), u: u.slice(0, 160) });
  });
  for (const url of ["https://ime.co.ir/PriceBoard", "https://ime.co.ir/"]) {
    try { await p.goto(url, { waitUntil: "networkidle", timeout: 60000 }); await p.waitForTimeout(4000); } catch (e) {}
  }
  console.log(JSON.stringify({ requests: hits.slice(0, 25), responses: resp.slice(0, 25) }, null, 1));
  await b.close();
})();
