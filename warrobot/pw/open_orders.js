/** Open orders (thin, self-contained, robust login). Prints {"orders":[...]}. */
const fs = require("fs"), path = require("path"), { execFileSync } = require("child_process");
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
  const ctx = await chromium.launchPersistentContext(PROFILE, {
    channel: "chrome", headless: true, args: ["--no-first-run"],
  });
  await SESSION.load(ctx);
  const page = ctx.pages()[0] || await ctx.newPage();

  async function setVal(sel, val) {
    return await page.evaluate(({ sel, val }) => {
      const el = document.querySelector(sel);
      if (!el) return false;
      const proto = el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype
                                                      : HTMLInputElement.prototype;
      Object.getOwnPropertyDescriptor(proto, "value").set.call(el, String(val));
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
      await page.waitForTimeout(2000);
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

  let orders = [];
  try {
    const raw = await page.evaluate(async (api) => {
      const r = await fetch(api + "/Order/GetOpenOrder/OpenOrder", { credentials: "include" });
      return await r.text();
    }, API);
    const d = JSON.parse(raw);
    orders = (d.Data || []).map((x) => ({
      symbol: x.symbol, isin: x.nsccode, orderid: String(x.orderid),
      side: x.orderside, qty: x.qunatity, executed: x.excuted,
      price: x.orderprice, status: x.status, state: x.state,
      time: x.time, timeTrade: x.dtime,
    }));
  } catch (e) {
    console.log(JSON.stringify({ orders: [], error: String(e).slice(0, 120) }));
    await SESSION.save(ctx).catch(()=>{});
 await ctx.close();
    return;
  }
  console.log(JSON.stringify({ orders }));
  await ctx.close();
})();
