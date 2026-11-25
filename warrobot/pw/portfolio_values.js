// Read the broker portfolio grid and return structured numbers.
// The API returns BuyPriceAvg=0 and no price, the DOM has both value and last price.
//   node portfolio_values.js
// -> {"positions":[{symbol,isin,qty,value,price,change_pct}], "at":...}
const { chromium } = require("playwright");
const fs = require("fs");
const path = require("path");
const { execFileSync } = require("child_process");

const BASE = path.join(__dirname, "..");
const PROFILE = path.join(BASE, "state", "pw-profile");
const OCR = path.join(BASE, "ocr", "read2.js");
const PNG = path.join(BASE, "data", "captcha_pw.png");
const CREDS = JSON.parse(fs.readFileSync(path.join(BASE, "state", "credentials.json"), "utf8"));

function solve(uri) {
  if (!uri || !uri.startsWith("data:image")) return "";
  fs.writeFileSync(PNG, Buffer.from(uri.split(",")[1], "base64"));
  try {
    const o = execFileSync(process.execPath, [OCR, PNG], { encoding: "utf8", timeout: 180000 });
    return String(JSON.parse((o || "{}").trim() || "{}").text || "");
  } catch (e) { return ""; }
}

function num(s) {
  if (!s) return 0;
  const m = String(s).replace(/[,\s]/g, "").match(/-?\d+(\.\d+)?/);
  return m ? parseFloat(m[0]) : 0;
}

(async () => {
  const ctx = await chromium.launchPersistentContext(PROFILE, {
    channel: "chrome", headless: true,
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
  if (await loginFormVisible()) { console.log(JSON.stringify({ positions: [], error: "login" })); await ctx.close(); return; }

  const raw = await page.evaluate(() => {
    const rows = [];
    document.querySelectorAll("ul.g-row").forEach((r) => {
      const id = r.id || "";
      if (!id.startsWith("row_")) return;
      const get = (cid) => {
        const el = r.querySelector('[id="' + cid + '"]');
        return el ? (el.innerText || "").replace(/\s+/g, " ").trim() : "";
      };
      rows.push({
        isin: id.replace("row_", ""),
        symbol: get("cell_SymbolFa"),
        qty: get("cell_RunTimeShareCount"),
        value: get("cell_PortfolioSellValue"),
        last: get("cell_LastTradedPriceVarPercent"),
        closing: get("cell_ClosingPriceVarPercent"),
        bestBuy: get("cell_BestBuyPrice"),
        bestSell: get("cell_BestSellPrice"),
      });
    });
    return rows;
  });

  const positions = raw.map((r) => {
    const lastTxt = String(r.last || "");
    const m = lastTxt.match(/([\d,]+)\s*%\s*\(?(-?[\d.]+)\)?/);
    return {
      symbol: r.symbol,
      isin: r.isin,
      qty: Math.round(num(r.qty)),
      value: Math.round(num(r.value)),
      price: m ? num(m[1]) : num(r.closing),
      change_pct: m ? parseFloat(m[2]) : 0,
      best_buy: num(r.bestBuy),
      best_sell: num(r.bestSell),
    };
  }).filter((p) => p.qty > 0);

  console.log(JSON.stringify({ positions, at: new Date().toISOString() }));
  await ctx.close();
})();
