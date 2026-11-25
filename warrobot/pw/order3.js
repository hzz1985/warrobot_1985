require("./cdp_shared"); // shared logged-in broker browser (Hark fix #2)
// Round 3: after clicking خرید, wait and watch what the broker reports.
// - full POST response
// - orderstatev6 updates (SignalR channel the panel subscribes to)
// - the "سفارشات امروز" table text after the wait
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

(async () => {
  const symbol = process.argv[2] || "آردینه";
  const wantQty = parseInt(process.argv[3] || "0", 10);
  const wantPrice = process.argv[4] ? parseFloat(process.argv[4]) : null;

  const ctx = await chromium.launchPersistentContext(PROFILE, {
    channel: "chrome", headless: false,
    args: ["--no-first-run", "--no-default-browser-check"],
  });
  const page = ctx.pages()[0] || (await ctx.newPage());
  const log = { posts: [], ws: [] };

  page.on("websocket", (ws) => {
    ws.on("framereceived", (f) => {
      const s = String(f.payload || "");
      if (/orderstate|Order/i.test(s)) log.ws.push(s.slice(0, 400));
    });
  });
  page.on("response", async (r) => {
    const u = r.url();
    if (u.includes("/Order/Post")) {
      try { log.posts.push({ status: r.status(), body: await r.text() }); } catch (e) {}
    }
  });

  // login (same session)
  await page.goto("https://online.meibours.ir/", { waitUntil: "domcontentloaded", timeout: 60000 });
  await page.waitForTimeout(3500);
  let captcha = "";
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
    for (let i = 0; i < 3; i++) {
      await page.click("#captcha-img").catch(() => {});
      await page.waitForTimeout(2500);
      captcha = solve(await page.getAttribute("#captcha-img", "src").catch(() => ""));
      if (captcha.length === 4) break;
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
    }, { u: CREDS.user, p: CREDS.password, c: captcha });
    await page.evaluate(() => { if (typeof submitForm === "function") submitForm(); else document.getElementById("main-form")?.click(); });
    await page.waitForTimeout(12000);
  }
  const out = { captcha, url: page.url(), symbol, bound: "", limit: 0, qty: 0, post: null, today: "", open: "" };
  if (await loginFormVisible()) { console.log(JSON.stringify(out)); await ctx.close(); return; }
  await page.waitForTimeout(2000);

  // open watchlist + bind
  await page.evaluate(() => {
    let el = null;
    document.querySelectorAll("span.lm_title").forEach((x) => { if ((x.innerText || "").trim() === "دیده بان") el = x; });
    if (el) { const t = el.closest("a,div,li") || el; ["mousedown","mouseup","click"].forEach((e) => t.dispatchEvent(new MouseEvent(e, { bubbles: true, cancelable: true, view: window }))); }
  });
  await page.waitForTimeout(2500);
  const box = page.locator("#txt_search");
  await box.scrollIntoViewIfNeeded().catch(() => {});
  await box.click({ timeout: 10000 }).catch(() => {});
  await page.keyboard.type(symbol, { delay: 45 });
  await page.waitForTimeout(2800);
  await page.evaluate((sym) => {
    let el = null;
    document.querySelectorAll("div,li,span").forEach((x) => { if (el) return; if ((x.innerText || "").trim() === sym && x.offsetParent !== null) el = x; });
    if (el) { const t = el.closest("li,a,div") || el; ["mousedown","mouseup","click"].forEach((e) => t.dispatchEvent(new MouseEvent(e, { bubbles: true, cancelable: true, view: window }))); }
  }, symbol);
  await page.waitForTimeout(3000);
  out.bound = await page.evaluate(() => (document.querySelector("#fulltextContainer")?.innerText || "").trim().slice(0, 12));
  const text = await page.evaluate(() => (document.body.innerText || "").replace(/\s+/g, " "));
  const m = text.match(/آستانه\s*حجم[^\d]{0,6}([\d,]+)\s*[-–]\s*([\d,]+)/);
  out.limit = m ? parseInt(m[2].replace(/,/g, ""), 10) : 0;
  out.qty = out.limit && wantQty ? Math.min(wantQty, out.limit) : wantQty;
  if (!out.qty) { console.log(JSON.stringify(out)); await ctx.close(); return; }

  await page.fill("#send_order_txtCount", String(out.qty)).catch(() => {});
  if (wantPrice) await page.fill("#send_order_txtPrice", String(wantPrice)).catch(() => {});
  await page.waitForTimeout(700);
  await page.locator("#send_order_btnBuySell").scrollIntoViewIfNeeded().catch(() => {});
  await page.locator("#send_order_btnBuySell").click({ timeout: 10000 }).catch(() => {});
  await page.waitForTimeout(15000);

  out.post = log.posts.slice(-1)[0] || null;
  out.ws = log.ws.slice(-3);
  out.today = await page.evaluate(() => {
    const t = (document.body.innerText || "").replace(/\s+/g, " ");
    const i = t.indexOf("سفارشات امروز");
    return i >= 0 ? t.slice(i, i + 260) : "";
  });
  out.open = await page.evaluate(async () => {
    const r = await fetch("https://api.meibours.ir/Web/V1/Order/GetOpenOrder/OpenOrder", { credentials: "include" });
    return (await r.text()).slice(0, 300);
  });
  console.log(JSON.stringify(out).slice(0, 1800));
  await ctx.close();
})();
