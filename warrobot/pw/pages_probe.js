// Probe the numbered panel pages (/Home/Default2, page-3..5) to find trade history.
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
    const o = execFileSync(process.execPath, [OCR, PNG], { encoding: "utf8", timeout: 180000 });
    return String(JSON.parse((o || "{}").trim() || "{}").text || "");
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

  const vis = async (s) => page.evaluate((sel) => {
    const a = Array.from(document.querySelectorAll(sel));
    return a.some((u) => {
      const r = u.getBoundingClientRect();
      return r.width > 0 && r.height > 0;
    });
  }, s);

  if (await vis("#username")) {
    let code = "";
    for (let i = 0; i < 4; i++) {
      await page.click("#captcha-img", { force: true }).catch(() => {});
      await page.waitForTimeout(2000);
      code = solve(await page.getAttribute("#captcha-img", "src").catch(() => ""));
      if (code.length === 4) break;
    }
    await page.evaluate(([u, p, c]) => {
      const set = (s, v) => {
        const el = document.querySelector(s); if (!el) return;
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

  const hrefs = ["/Home/Default2", "/Home/Index/page-3", "/Home/Index/page-4", "/Home/Index/page-5"];
  for (const u of hrefs) {
    await page.evaluate((h) => {
      const a = document.querySelector('a[href="' + h + '"]');
      if (a) ["mousedown","mouseup","click"].forEach((e) =>
        a.dispatchEvent(new MouseEvent(e, { bubbles: true, cancelable: true, view: window })));
    }, u);
    await page.waitForTimeout(4000);
    const info = await page.evaluate(() => {
      const vis = (e) => {
        const r = e.getBoundingClientRect();
        return r.width > 0 && r.height > 0;
      };
      const heads = [];
      document.querySelectorAll("table tr:first-child, h1, h2, h3").forEach((e) => {
        if (!vis(e)) return;
        const t = (e.innerText || "").replace(/\s+/g, " ").trim();
        if (t && t.length < 120) heads.push(t);
      });
      return { url: location.href, heads: heads.slice(0, 6),
               body: (document.body.innerText || "").replace(/\s+/g, " ").slice(0, 180) };
    });
    console.log(JSON.stringify(info));
  }
  await ctx.close();
})().catch((e) => { console.log("ERR", String(e).slice(0, 200)); process.exit(1); });
