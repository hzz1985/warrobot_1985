require("./cdp_shared"); // shared logged-in broker browser (Hark fix #2)
// Probe, inside one Playwright session: login -> inspect the panel DOM.
// Prints where the order form lives so the order step can use the same path.
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

async function login(page) {
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
  if (code.length !== 4) return { ok: false, reason: "captcha" };
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

(async () => {
  const ctx = await chromium.launchPersistentContext(PROFILE, {
    channel: "chrome", headless: false,
    args: ["--no-first-run", "--no-default-browser-check"],
  });
  const page = ctx.pages()[0] || (await ctx.newPage());
  const lr = await login(page);
  console.log("login:", JSON.stringify(lr));

  const snap = async (label) => {
    const s = await page.evaluate(() => {
      const box = (sel) => { const e = document.querySelector(sel); return e ? Math.round(e.getBoundingClientRect().width) : -1; };
      const titles = [];
      document.querySelectorAll("span.lm_title").forEach((x) => { const t = (x.innerText || "").trim(); if (t) titles.push(t); });
      return {
        url: location.href,
        count: box("#send_order_txtCount"),
        price: box("#send_order_txtPrice"),
        search: box("#txt_search"),
        titles: Array.from(new Set(titles)),
      };
    });
    console.log(label, JSON.stringify(s));
    return s;
  };

  // never navigate after login: the SPA route must be clicked
  await snap("after login:");
  await page.evaluate(() => {
    let el = null;
    document.querySelectorAll("span.lm_title").forEach((x) => { if ((x.innerText || "").trim() === "ارسال سفارش") el = x; });
    if (el) { const t = el.closest("a,div,li") || el; ["mousedown","mouseup","click"].forEach((e) => t.dispatchEvent(new MouseEvent(e, { bubbles: true, cancelable: true, view: window }))); }
  });
  await page.waitForTimeout(3000);
  await snap("after ارسال سفارش:");
  await page.evaluate(() => {
    let el = null;
    document.querySelectorAll("span.lm_title").forEach((x) => { if ((x.innerText || "").trim() === "دیده بان") el = x; });
    if (el) { const t = el.closest("a,div,li") || el; ["mousedown","mouseup","click"].forEach((e) => t.dispatchEvent(new MouseEvent(e, { bubbles: true, cancelable: true, view: window }))); }
  });
  await page.waitForTimeout(3000);
  await snap("page-1 + دیده بان:");
  await ctx.close();
})();
