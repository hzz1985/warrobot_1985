const { chromium } = require("playwright");
(async () => {
  const b = await chromium.launch({ channel: "chrome", headless: true });
  const p = await b.newPage({ viewport: { width: 1400, height: 1100 } });
  const r = await p.goto("http://localhost/", { waitUntil: "networkidle", timeout: 90000 });
  const info = await p.evaluate(() => {
    const notes = Array.from(document.querySelectorAll("p.note")).map((n) => n.innerText.replace(/\s+/g, " ").trim());
    const rows = Array.from(document.querySelectorAll("table tr")).map((t) => t.innerText.replace(/\s+/g, " ").trim()).filter((t) => t.includes("آواپارس"));
    return { status: document.readyState, note: notes.find((n) => n.includes("آخرین به‌روزرسانی")), rows, refresh: !!document.querySelector('meta[http-equiv="refresh"]') };
  });
  console.log(JSON.stringify({ http: r.status(), ...info }, null, 1).slice(0, 900));
  await p.screenshot({ path: "C:\\Users\\Administrator\\Desktop\\DeepSeek DeskTop\\WarRobot\\state\\web_pnl.png" });
  await b.close();
})();
