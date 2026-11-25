// Pull the customer's trade history (fills) from the broker API.
//   node trades.js [days]
// Prints one JSON line: {trades:[{symbol,isin,side,qty,price,date,time}], raw:...}
const { chromium } = require("playwright");
const fs = require("fs");
const path = require("path");
const { execFileSync } = require("child_process");

const BASE = path.join(__dirname, "..");
const PROFILE = path.join(BASE, "state", "pw-profile");
const OCR = path.join(BASE, "ocr", "read2.js");
const PNG = path.join(BASE, "data", "captcha_pw.png");
const CREDS = JSON.parse(fs.readFileSync(path.join(BASE, "state", "credentials.json"), "utf8"));
const API = "https://api.meibours.ir/Web/V1";

function solve(uri) {
  if (!uri || !uri.startsWith("data:image")) return "";
  fs.writeFileSync(PNG, Buffer.from(uri.split(",")[1], "base64"));
  try {
    const o = execFileSync(process.execPath, [OCR, PNG], { encoding: "utf8", timeout: 180000 });
    return String(JSON.parse((o || "{}").trim() || "{}").text || "");
  } catch (e) { return ""; }
}

(async () => {
  const ctx = await chromium.launchPersistentContext(PROFILE, {
    channel: "chrome", headless: false,
    args: ["--no-first-run", "--no-default-browser-check"],
  });
  const page = ctx.pages()[0] || (await ctx.newPage());
  await page.goto("https://online.meibours.ir/", { waitUntil: "domcontentloaded", timeout: 60000 });
  await page.waitForTimeout(3500);
  async function loginFormVisible() {
    return await page.evaluate(() => {
      if (!/login/i.test(location.href)) return false;
      const u = document.querySelector("#username");
      if (!u) return false;
      const r = u.getBoundingClientRect();
      const st = window.getComputedStyle(u);
      return r.width > 0 && r.height > 0 && st.visibility !== "hidden" && st.display !== "none";
    });
  }
  if (await loginFormVisible()) {
    let code = "";
    for (let i = 0; i < 6; i++) {
      await page.click("#captcha-img").catch(() => {});
      await page.waitForTimeout(2500);
      code = solve(await page.getAttribute("#captcha-img", "src").catch(() => ""));
      if (code.length === 4) break;
    }
    await page.evaluate(({ u, p, c }) => {
      const set = (sel, val) => {
        const el = document.querySelector(sel);
        if (!el) return;
        const proto = el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype
                                                        : HTMLInputElement.prototype;
        Object.getOwnPropertyDescriptor(proto, "value").set.call(el, String(val));
        el.dispatchEvent(new Event("input", { bubbles: true }));
        el.dispatchEvent(new Event("change", { bubbles: true }));
      };
      set("#username", u); set("#password", p); set("#captcha", c);
    }, { u: CREDS.user, p: CREDS.password, c: code });
    await page.evaluate(() => { if (typeof submitForm === "function") submitForm(); else document.getElementById("main-form")?.click(); });
    await page.waitForTimeout(12000);
  }
  if (await loginFormVisible()) { console.log(JSON.stringify({ trades: [], error: "login" })); await ctx.close(); return; }

  const res = await page.evaluate(async (api) => {
    const out = { trades: [], tried: [] };
    const urls = [
      api + "/Order/GetCustomerTrades/Customer/GetCustomerTrades",
      api + "/Order/GetCustomerTrades/Customer/GetCustomerTrades?fromDate=1700000000&toDate=1900000000",
      api + "/Order/GetCustomerTrades",
    ];
    for (const u of urls) {
      try {
        const r = await fetch(u, { credentials: "include" });
        const txt = await r.text();
        out.tried.push({ u: u.split("/").slice(-2).join("/"), status: r.status, len: txt.length });
        if (r.status !== 200) continue;
        const d = JSON.parse(txt);
        const rows = d.Data || d.data || [];
        if (!Array.isArray(rows) || !rows.length) continue;
        out.trades = rows.map((x) => ({
          symbol: x.symbol || x.Symbol || x.insCodeFa,
          isin: x.isin || x.ISIN || x.InsCode,
          side: x.side || x.Side || x.orderSide || x.OrderSide,
          qty: x.qty || x.quantity || x.Quantity || x.Qty,
          price: x.price || x.Price || x.TradePrice,
          date: x.date || x.Date || x.TradeDate,
          time: x.time || x.Time,
        }));
        out.rawFirst = rows[0];
        break;
      } catch (e) {
        out.tried.push({ u, error: String(e).slice(0, 80) });
      }
    }
    return out;
  }, API);

  console.log(JSON.stringify(res).slice(0, 2500));
  await ctx.close();
})();
