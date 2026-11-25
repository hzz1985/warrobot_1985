// Probe: list the broker panel menu links to find the trade-history page.
const { chromium } = require("playwright");
const path = require("path");
const fs = require("fs");
const { execFileSync } = require("child_process");

const BASE = path.join(__dirname, "..");
const PROFILE = path.join(BASE, "state", "pw-profile");
const CREDS = JSON.parse(fs.readFileSync(path.join(BASE, "state", "credentials.json"), "utf8"));
const OCR = path.join(BASE, "ocr", "read2.js");
const PNG = path.join(BASE, "data", "captcha_pw.png");

function solve(uri) {
  if (!uri || !uri.startsWith("data:image")) return "";
  fs.writeFileSync(PNG, Buffer.from(uri.split(",")[1], "base64"));
  try {
    const out = execFileSync(process.execPath, [OCR, PNG], { encoding: "utf8", timeout: 180000 });
    return String(JSON.parse((out || "{}").trim() || "{}").text || "");
  } catch { return ""; }
}

(async () => {
  const ctx = await chromium.launchPersistentContext(PROFILE, {
    channel: "chrome", headless: false,
    args: ["--no-first-run", "--no-default-browser-check"],
  });
  const page = ctx.pages()[0] || (await ctx.newPage());
  await page.goto("https://online.meibours.ir/", { waitUntil: "domcontentloaded", timeout: 60000 });
  await page.waitForTimeout(3500);

  const vis = async (sel) => page.evaluate((s) => {
    const u = document.querySelector(s);
    if (!u) return false;
    const r = u.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  }, sel);

  if (await vis("#username")) {
    let code = "";
    for (let i = 0; i < 4; i++) {
      await page.click("#captcha-img", { force: true }).catch(() => {});
      await page.waitForTimeout(2000);
      code = solve(await page.getAttribute("#captcha-img", "src").catch(() => ""));
      if (code.length === 4) break;
    }
    await page.evaluate(([u, p, c]) => {
      const set = (sel, v) => {
        const el = document.querySelector(sel); if (!el) return;
        const d = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set;
        d.call(el, v); el.dispatchEvent(new Event("input", { bubbles: true }));
        el.dispatchEvent(new Event("change", { bubbles: true }));
      };
      set("#username", u); set("#password", p); set("#captcha", c);
      if (typeof submitForm === "function") submitForm();
      else document.getElementById("main-form")?.click();
    }, [CREDS.user, CREDS.password, code]);
    await page.waitForTimeout(12000);
  }
  console.log("URL", page.url());

  await page.evaluate(() => {
    const li = Array.from(document.querySelectorAll("li,span,div"))
      .find((x) => (x.innerText || "").trim() === "معاملات امروز");
    if (li) {
      ["mousedown","mouseup","click"].forEach((e) =>
        li.dispatchEvent(new MouseEvent(e, { bubbles: true, cancelable: true, view: window })));
    }
  });
  await page.waitForTimeout(3500);
  const panel = await page.evaluate(() => {
    const vis = (e) => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
    const tables = Array.from(document.querySelectorAll("table")).filter(vis)
      .map((t) => (t.innerText || "").replace(/\s+/g, " ").slice(0, 400));
    const inputs = Array.from(document.querySelectorAll("input")).filter(vis)
      .map((i) => ({ id: i.id, ph: i.placeholder, val: i.value })).slice(0, 12);
    return { url: location.href, tables: tables.slice(0, 4), inputs };
  });
  console.log("PANEL", JSON.stringify(panel, null, 1).slice(0, 3000));

  const links = await page.evaluate(() => {
    const out = [];
    document.querySelectorAll("a,li,span,div").forEach((e) => {
      const t = (e.innerText || "").replace(/\s+/g, " ").trim();
      if (!t || t.length > 30) return;
      if (!/سابقه|معاملات|تاریخ|گزارش|کارگزار/.test(t)) return;
      const r = e.getBoundingClientRect();
      if (!r.width || !r.height) return;
      if (e.tagName === "A") out.push({ t, href: e.getAttribute("href") });
      else out.push({ t, href: null, tag: e.tagName });
    });
    const uniq = [];
    const seen = new Set();
    out.forEach((x) => { const k = x.t + "|" + (x.href || ""); if (!seen.has(k)) { seen.add(k); uniq.push(x); } });
    return uniq.slice(0, 40);
  });
  console.log(JSON.stringify(links, null, 1).slice(0, 4000));
  await ctx.close();
})().catch((e) => { console.log("ERR", String(e).slice(0, 300)); process.exit(1); });
