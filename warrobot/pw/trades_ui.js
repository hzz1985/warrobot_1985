// UI reader for the broker's today-trades table (source 4 for a11).
// Prints JSON: {"rows":[{symbol,qty,price,side,date}]}
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
  const symbol = process.argv[2] || "";
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

  const rows = await page.evaluate((want) => {
    const norm = (t) => String(t || "").replace(/[\u200c\u200f\u200e\u00a0]/g, "")
      .replace(/[ي]/g, "ی").replace(/[ك]/g, "ک").replace(/\s+/g, " ").trim();
    const out = [];
    const vis = (e) => {
      const r = e.getBoundingClientRect();
      return r.width > 0 && r.height > 0;
    };
    document.querySelectorAll("table").forEach((t) => {
      if (!vis(t)) return;
      t.querySelectorAll("tr").forEach((tr) => {
        const cells = Array.from(tr.querySelectorAll("td")).map((td) => norm(td.innerText));
        if (cells.length < 4) return;
        const text = norm(tr.innerText);
        if (want && !text.includes(norm(want))) return;
        const nums = cells.map((c) => c.replace(/,/g, "")).filter((c) => /^\d+(\.\d+)?$/.test(c)).map(Number);
        if (nums.length < 2) return;
        out.push({ row: text.slice(0, 120), nums });
      });
    });
    return out;
  }, symbol);

  console.log(JSON.stringify({ count: rows.length, rows: rows.slice(0, 20),
                               url: page.url() }));
  await ctx.close();
})().catch((e) => { console.log(JSON.stringify({ error: String(e).slice(0, 200) })); process.exit(1); });
