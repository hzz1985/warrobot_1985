const { chromium } = require("playwright");
(async () => {
  const b = await chromium.launch({ channel: "chrome", headless: true });
  const p = await b.newPage({ viewport: { width: 1280, height: 900 } });
  const api = [];
  p.on("response", async (r) => {
    if (r.url().includes("search.codal.ir/api")) api.push({ u: r.url().slice(0, 120), s: r.status() });
  });
  try {
    await p.goto("https://search.codal.ir/", { waitUntil: "domcontentloaded", timeout: 60000 });
    await p.waitForTimeout(4000);
    const txt = await p.evaluate(() => document.body.innerText.slice(0, 300));
    console.log(JSON.stringify({ loaded: true, txt, api: api.slice(0, 6) }));
  } catch (e) {
    console.log(JSON.stringify({ loaded: false, err: String(e).slice(0, 120), api: api.slice(0, 6) }));
  }
  await b.close();
})();
