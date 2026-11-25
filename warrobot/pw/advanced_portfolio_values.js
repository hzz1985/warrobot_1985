require("./cdp_shared"); // shared logged-in broker browser (Hark fix #2)
// Authoritative P/L per position from the broker's advanced realtime portfolio
// (/Home/RealtimePortfolioAdvanced). It exposes the REAL average cost, the
// current price and the exact profit/loss the operator sees in the panel.
//   node advanced_portfolio_values.js
// -> {"positions":[{symbol,qty,avg_cost,avg_cost_fee,price,value,pnl_rial,pnl_pct,pnl_fee_pct}],...}
const { chromium } = require("playwright");
const SESSION = require("./session");
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
  if (s === undefined || s === null) return 0;
  const txt = String(s).replace(/[,\s%]/g, "");
  const neg = /^\(.*\)$/.test(txt) || txt.startsWith("-");
  const m = txt.replace(/[()]/g, "").match(/-?\d+(\.\d+)?/);
  if (!m) return 0;
  const v = parseFloat(m[0]);
  return neg ? -Math.abs(v) : v;
}

(async () => {
  const ctx = await chromium.launchPersistentContext(PROFILE, {
    channel: "chrome", headless: true,
    args: ["--no-first-run", "--no-default-browser-check"],
  });
  await SESSION.load(ctx);
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
  if (await SESSION.needLogin(page)) {
  const _gate = SESSION.loginAllowed();
  if (!_gate.ok) {
    console.log(JSON.stringify({ message: "login_blocked", reason: _gate.reason, day: _gate.day }));
    await ctx.close();
    return;
  }
  SESSION.noteLogin();
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
  if (await SESSION.needLogin(page)) { console.log(JSON.stringify({ positions: [], error: "login" })); await SESSION.save(ctx).catch(()=>{});
 await ctx.close(); return; }

  const ok = await page.evaluate(() => {
    const a = document.querySelector('a[href="RealtimePortfolioAdvanced"]');
    if (!a) return false;
    ["mousedown", "mouseup", "click"].forEach((e) =>
      a.dispatchEvent(new MouseEvent(e, { bubbles: true, cancelable: true, view: window })));
    return true;
  });
  await page.waitForTimeout(8000);

  const raw = await page.evaluate(() => {
    const rows = [];
    document.querySelectorAll("ul.g-row").forEach((r) => {
      const cells = [];
      r.querySelectorAll("li").forEach((li) => cells.push((li.innerText || "").replace(/\s+/g, " ").trim()));
      if (cells.length >= 12 && cells[1]) rows.push(cells.slice(0, 18));
    });
    return rows;
  });

  const positions = raw.map((c) => ({
    symbol: c[1],
    qty: Math.round(num(c[2])),
    cost_value: num(c[3]),
    avg_cost: num(c[4]),
    price: num(c[5]),
    value: num(c[6]),
    avg_cost_fee: num(c[7]),
    today_pnl: num(c[8]),
    pnl_rial: num(c[10]),
    pnl_pct: num(c[11]),
    pnl_fee_rial: num(c[12]),
    pnl_fee_pct: num(c[13]),
  })).filter((p) => p.qty > 0 && p.symbol);

  console.log(JSON.stringify({ clicked: ok, url: page.url(), positions }));
  await ctx.close();
})();
