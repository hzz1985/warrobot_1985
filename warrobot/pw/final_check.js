const { chromium } = require("playwright");
(async () => {
  const b = await chromium.launch({ channel: "chrome", headless: true });
  const p = await b.newPage({ viewport: { width: 1400, height: 1100 } });
  await p.goto("http://localhost/", { waitUntil: "networkidle", timeout: 90000 });
  await p.waitForTimeout(1200);
  const info = await p.evaluate(() => {
    const rows = [];
    document.querySelectorAll("tr").forEach((tr) => {
      const t = (tr.innerText || "").replace(/\s+/g, " ").trim();
      if (/^(افران|آواپارس|خساپا|آردینه)\b/.test(t)) rows.push(t.slice(0, 150));
    });
    const svg = document.querySelector("svg");
    return { rows, hasChart: !!svg, svgLen: svg ? svg.outerHTML.length : 0,
             title: document.title };
  });
  console.log(JSON.stringify(info, null, 1).slice(0, 2000));
  await b.close();
})();
