// Probe: discover the route to the broker's trade-history page.
// Dumps nav items (text + href/onclick) and tries plausible ones, printing URLs.
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
    const all = Array.from(document.querySelectorAll(s));
    return all.some((u) => {
      const r = u.getBoundingClientRect();
      return r.width > 0 && r.height > 0;
    });
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
  console.log("START", page.url());

  const nav = await page.evaluate(() => {
    const out = [];
    const vis = (e) => {
      const r = e.getBoundingClientRect();
      return r.width > 0 && r.height > 0;
    };
    document.querySelectorAll("a,li,span,div,button").forEach((e) => {
      const t = (e.innerText || "").replace(/\s+/g, " ").trim();
      if (!t || t.length > 24) return;
      if (!vis(e)) return;
      const href = e.getAttribute("href") || "";
      const oc = e.getAttribute("onclick") || "";
      if (!href && !oc && e.tagName !== "LI") return;
      out.push({ tag: e.tagName, t, href, oc: oc.slice(0, 80) });
    });
    const seen = new Set();
    return out.filter((x) => { const k = x.t + x.href + x.oc; if (seen.has(k)) return false; seen.add(k); return true; }).slice(0, 60);
  });
  console.log("NAV", JSON.stringify(nav).slice(0, 4000));

  // click each promising item and record the resulting URL
  const tried = [];
  for (const label of ["سابقه معاملات", "سابقه", "معاملات", "گزارش", "صورت حساب", "حساب امروز", "معاملات امروز"]) {
    const res = await page.evaluate((want) => {
      const vis = (e) => {
        const r = e.getBoundingClientRect();
        return r.width > 0 && r.height > 0;
      };
      const el = Array.from(document.querySelectorAll("a,li,span,div,button"))
        .find((x) => vis(x) && (x.innerText || "").replace(/\s+/g, " ").trim() === want);
      if (!el) return { clicked: false };
      ["mousedown", "mouseup", "click"].forEach((e) =>
        el.dispatchEvent(new MouseEvent(e, { bubbles: true, cancelable: true, view: window })));
      return { clicked: true };
    }, label);
    await page.waitForTimeout(2200);
    tried.push({ label, ...res, url: page.url() });
    if (res.clicked && !/Default\/page-1/.test(page.url())) {
      const body = await page.evaluate(() => (document.body.innerText || "").replace(/\s+/g, " ").slice(0, 260));
      tried[tried.length - 1].body = body;
      break;
    }
  }
  console.log("TRIED", JSON.stringify(tried, null, 1).slice(0, 3000));
  await ctx.close();
})().catch((e) => { console.log("ERR", String(e).slice(0, 300)); process.exit(1); });
