require("./cdp_shared"); // shared logged-in broker browser (Hark fix #2)
// Place a buy order inside ONE Playwright session: login (if needed) -> bind the
// symbol via the watchlist search -> read the ticket limit -> fill -> click خرید.
// Never navigates after login: a full page load drops the broker session.
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
    const out = execFileSync(process.execPath, [OCR, PNG], { encoding: "utf8", timeout: 180000 });
    return String(JSON.parse((out || "{}").trim() || "{}").text || "");
  } catch (e) { return ""; }
}

async function loginIfNeeded(page) {
  await page.goto("https://online.meibours.ir/", { waitUntil: "domcontentloaded", timeout: 60000 });
  await page.waitForTimeout(3500);
  if (!page.url().includes("Login")) return { ok: true, already: true };
  let code = "";
  for (let i = 0; i < 3; i++) {
    await page.click("#captcha-img").catch(() => {});
    await page.waitForTimeout(2500);
    code = solve(await page.getAttribute("#captcha-img", "src").catch(() => ""));
    if (code.length === 4) break;
  }
  if (code.length !== 4) return { ok: false, reason: "captcha_unreadable" };
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
  return { ok: !page.url().includes("Login"), captcha: code, url: page.url() };
}

const clickTitle = (page, label) =>
  page.evaluate((lbl) => {
    let el = null;
    document.querySelectorAll("span.lm_title").forEach((x) => { if ((x.innerText || "").trim() === lbl) el = x; });
    if (!el) return false;
    const t = el.closest("a,div,li") || el;
    ["mousedown", "mouseup", "click"].forEach((e) => t.dispatchEvent(new MouseEvent(e, { bubbles: true, cancelable: true, view: window })));
    return true;
  }, label);

(async () => {
  const symbol = process.argv[2] || "آباد";
  const wantQty = parseInt(process.argv[3] || "0", 10);
  const wantPrice = process.argv[4] ? parseFloat(process.argv[4]) : null;

  const ctx = await chromium.launchPersistentContext(PROFILE, {
    channel: "chrome", headless: false,
    args: ["--no-first-run", "--no-default-browser-check"],
  });
  const page = ctx.pages()[0] || (await ctx.newPage());
  const net = [];
  page.on("request", (r) => {
    const u = r.url();
    if (/\/Order\/|InsertOrder|SendOrder|Trade/i.test(u)) net.push({ req: r.method() + " " + u.slice(-70), body: (r.postData() || "").slice(0, 1400) });
  });
  page.on("response", async (r) => {
    const u = r.url();
    if (/\/Order\/|InsertOrder|SendOrder/i.test(u)) {
      try { net.push({ resp: u.slice(-70), status: r.status(), body: (await r.text()).slice(0, 260) }); } catch (e) {}
    }
  });

  const lr = await loginIfNeeded(page);
  const out = { login: lr, symbol, qty: 0, bound: "", submitted: false, message: "", net: [] };
  if (!lr.ok) { console.log(JSON.stringify(out)); await ctx.close(); return; }
  await page.waitForTimeout(2500);

  await clickTitle(page, "دیده بان");
  await page.waitForTimeout(2500);

  const searchW = await page.evaluate(() => { const e = document.querySelector("#txt_search"); return e ? Math.round(e.getBoundingClientRect().width) : -1; });
  const box = page.locator("#txt_search");
  await box.scrollIntoViewIfNeeded().catch(() => {});
  await box.click({ timeout: 10000 }).catch(() => {});
  await page.keyboard.type(symbol, { delay: 45 });
  await page.waitForTimeout(2800);

  const clicked = await page.evaluate((sym) => {
    let el = null;
    document.querySelectorAll("div,li,span").forEach((x) => {
      if (el) return;
      if ((x.innerText || "").trim() === sym && x.offsetParent !== null) el = x;
    });
    if (!el) return false;
    const t = el.closest("li,a,div") || el;
    ["mousedown", "mouseup", "click"].forEach((e) => t.dispatchEvent(new MouseEvent(e, { bubbles: true, cancelable: true, view: window })));
    return true;
  }, symbol);
  await page.waitForTimeout(3000);
  out.bound = await page.evaluate(() => (document.querySelector("#fulltextContainer")?.innerText || "").trim().slice(0, 12));
  out.searchW = searchW;
  out.clickedResult = clicked;

  const text = await page.evaluate(() => (document.body.innerText || "").replace(/\s+/g, " "));
  const m = text.match(/آستانه\s*حجم[^\d]{0,6}([\d,]+)\s*[-–]\s*([\d,]+)/);
  const limit = m ? parseInt(m[2].replace(/,/g, ""), 10) : 0;
  out.limit = limit;
  out.qty = limit && wantQty ? Math.min(wantQty, limit) : wantQty;

  if (!out.qty) { console.log(JSON.stringify(out)); await ctx.close(); return; }

  await page.fill("#send_order_txtCount", String(out.qty)).catch(() => {});
  if (wantPrice) await page.fill("#send_order_txtPrice", String(wantPrice)).catch(() => {});
  await page.waitForTimeout(700);
  out.fields = await page.evaluate(() => [
    document.querySelector("#send_order_txtCount")?.value ?? null,
    document.querySelector("#send_order_txtPrice")?.value ?? null,
  ]);

  // real mouse click on the buy button
  const btn = page.locator("#send_order_btnBuySell");
  await btn.scrollIntoViewIfNeeded().catch(() => {});
  await btn.click({ timeout: 10000 }).catch(async () => {
    await page.evaluate(() => document.getElementById("send_order_btnBuySell")?.click());
  });
  await page.waitForTimeout(7000);

  out.message = await page.evaluate(() => {
    let o = "";
    document.querySelectorAll("div,span,p").forEach((x) => {
      if (x.children.length) return;
      const t = (x.innerText || "").trim();
      if (/موفق|ثبت شد|عدم پذیرش|خطا|کافی/.test(t) && !/^مجاز$/.test(t) && t.length < 140) o = t;
    });
    return o;
  });
  out.submitted = /موفق|ثبت شد/.test(out.message);
  out.net = net.slice(-6);
  console.log(JSON.stringify(out));
  await ctx.close();
})();
