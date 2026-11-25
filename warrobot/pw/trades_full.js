// Pull the trade list from the panel's own endpoint and try to widen the range.
//   node trades_full.js
// -> {count, keys, rows:[...], tried:[...]}
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
  if (await loginFormVisible()) { console.log(JSON.stringify({ error: "login" })); await ctx.close(); return; }

  // open the trades stack so the app initialises its session for that endpoint
  await page.evaluate(() => {
    let el = null;
    document.querySelectorAll("span.lm_title").forEach((x) => {
      if ((x.innerText || "").trim() === "معاملات امروز") el = x;
    });
    if (el) {
      const t = el.closest("a,div,li") || el;
      ["mousedown", "mouseup", "click"].forEach((e) =>
        t.dispatchEvent(new MouseEvent(e, { bubbles: true, cancelable: true, view: window })));
    }
  });
  await page.waitForTimeout(3000);

  const res = await page.evaluate(async (api) => {
    const out = { tried: [], rows: [] };
    const base = api + "/Order/GetCustomerTrades/Customer/GetCustomerTrades";
    const variants = [
      base,
      base + "?fromDate=1700000000&toDate=1900000000",
      base + "?fromDate=&toDate=",
    ];
    for (const u of variants) {
      try {
        const r = await fetch(u, { credentials: "include" });
        const txt = await r.text();
        let d = null;
        try { d = JSON.parse(txt); } catch (e) {}
        const rows = (d && d.Data) || [];
        out.tried.push({ u: u.replace(api, ""), status: r.status, rows: rows.length });
        if (rows.length > (out.rows || []).length) {
          out.rows = rows;
          out.keys = Object.keys(rows[0] || {});
        }
      } catch (e) {
        out.tried.push({ u: u.replace(api, ""), error: String(e).slice(0, 60) });
      }
    }
    return out;
  }, API);

  console.log(JSON.stringify({ tried: res.tried, count: (res.rows || []).length,
                               keys: res.keys, rows: (res.rows || []).slice(0, 8) }
                             ).slice(0, 3000));
  fs.writeFileSync(path.join(BASE, "state", "trades_raw.json"),
                   JSON.stringify(res, null, 1), "utf8");
  await ctx.close();
})();
