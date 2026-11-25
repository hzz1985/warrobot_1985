require("./cdp_shared"); // shared logged-in broker browser (Hark fix #2)
/**
 * Robust broker client: login (native setter) + one API call chosen by argument.
 * Replaces the fragile copies scattered in pw/*.js.
 *
 *   node broker_client.js open        -> open orders
 *   node broker_client.js trades      -> today's trades
 *   node broker_client.js account     -> balances
 */
const fs = require("fs");
const path = require("path");
const { execFileSync } = require("child_process");
const { chromium } = require("playwright");
const SESSION = require("./session");

const BASE = path.join(__dirname, "..");
const PROFILE = path.join(BASE, "state", "pw-profile");
const CREDS = JSON.parse(fs.readFileSync(path.join(BASE, "state", "credentials.json"), "utf8"));
const OCR = path.join(BASE, "ocr", "read2.js");
const PNG = path.join(BASE, "data", "captcha_pw.png");
const API = "https://api.meibours.ir/Web/V1";

function solve(uri) {
  if (!uri || !uri.startsWith("data:image")) return "";
  fs.writeFileSync(PNG, Buffer.from(uri.split(",")[1], "base64"));
  try {
    const out = execFileSync(process.execPath, [OCR, PNG], { encoding: "utf8", timeout: 180000 });
    return String(JSON.parse((out || "{}").trim() || "{}").text || "");
  } catch (e) { return ""; }
}

(async () => {
  const what = (process.argv[2] || "open").toLowerCase();
  const ctx = await chromium.launchPersistentContext(PROFILE, {
    channel: "chrome", headless: true,
    args: ["--no-first-run"],
  });
  await SESSION.load(ctx);
  const page = ctx.pages()[0] || await ctx.newPage();

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
async function setVal(sel, val) {
    return await page.evaluate(({ sel, val }) => {
      const el = document.querySelector(sel);
      if (!el) return false;
      const proto = el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype
                                                      : HTMLInputElement.prototype;
      const setter = Object.getOwnPropertyDescriptor(proto, "value").set;
      setter.call(el, String(val));
      el.dispatchEvent(new Event("input", { bubbles: true }));
      el.dispatchEvent(new Event("change", { bubbles: true }));
      return true;
    }, { sel, val });
  }

  await page.goto("https://online.meibours.ir/", { waitUntil: "domcontentloaded", timeout: 60000 });
  await page.waitForTimeout(3500);
  const needs = await page.evaluate(() => {
    if (!/login/i.test(location.href)) return false;
    const u = document.querySelector("#username");
    if (!u) return false;
    const r = u.getBoundingClientRect();
    const st = window.getComputedStyle(u);
    return r.width > 0 && r.height > 0 && st.visibility !== "hidden" && st.display !== "none";
  });
  if (needs) {
    let code = "";
    for (let i = 0; i < 4; i++) {
      await page.click("#captcha-img", { force: true }).catch(() => {});
      await page.waitForTimeout(1800);
      code = solve(await page.getAttribute("#captcha-img", "src").catch(() => ""));
      if (code.length === 4) break;
    }
    await setVal("#username", CREDS.user);
    await setVal("#password", CREDS.password);
    await setVal("#captcha", code);
    await page.evaluate(() => {
      if (typeof submitForm === "function") submitForm();
      else document.getElementById("main-form")?.click();
    });
    await page.waitForTimeout(12000);
  }

  const result = { login_form: needs, url: page.url() };
  if (what === "open") {
    result.orders = await page.evaluate(async (api) => {
      try {
        const r = await fetch(api + "/Order/GetOpenOrder/OpenOrder", { credentials: "include" });
        const d = JSON.parse(await r.text());
        return (d.Data || []).map((x) => ({
          symbol: x.symbol, isin: x.nsccode, orderid: String(x.orderid),
          side: x.orderside, qty: x.qunatity, executed: x.excuted,
          price: x.orderprice, status: x.status, state: x.state,
          time: x.time, timeTrade: x.dtime,
        }));
      } catch (e) { return []; }
    }, API);
  } else if (what === "trades") {
    result.trades = await page.evaluate(async (api) => {
      try {
        const r = await fetch(api + "/Order/GetCustomerTrades/Customer/GetCustomerTrades",
                              { credentials: "include" });
        const d = JSON.parse(await r.text());
        return d.Data || [];
      } catch (e) { return []; }
    }, API);
    result.count = result.trades.length;
  } else {
    result.account = await page.evaluate(async (api) => {
      try {
        const r = await fetch(api + "/Accounting/Remain", { credentials: "include" });
        const d = JSON.parse(await r.text());
        return d.Data || {};
      } catch (e) { return {}; }
    }, API);
  }

  console.log(JSON.stringify(result));
  await SESSION.save(ctx).catch(()=>{});
 await ctx.close();
})();
