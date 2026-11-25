// Probe why the symbol search does not bind for certain names.
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
  const symbol = process.argv[2] || "آبادا";
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
    const st = window.getComputedStyle(u);
    return r.width > 0 && r.height > 0 && st.visibility !== "hidden" && st.display !== "none";
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

  const tabs = await page.evaluate(() =>
    Array.from(document.querySelectorAll("span.lm_title")).map((x) => (x.innerText || "").trim()));
  console.log("TABS", JSON.stringify(tabs));

  const searchVisible = await vis("#txt_search");
  console.log("search visible", searchVisible);

  if (searchVisible) {
    const box = page.locator("#txt_search");
    await box.click({ timeout: 15000 }).catch(() => {});
    await box.fill("").catch(() => {});
    await page.keyboard.type(symbol, { delay: 60 });
    await page.waitForTimeout(3000);
    const after = await page.evaluate(() => {
      const vis = (e) => e && e.offsetParent !== null;
      const items = [];
      document.querySelectorAll("li,div,span,tr").forEach((x) => {
        const t = (x.innerText || "").replace(/\s+/g, " ").trim();
        if (vis(x) && t && t.length < 60 && t.includes("آباد")) items.push(t.slice(0, 40));
      });
      return { typed: document.querySelector("#txt_search")?.value || "",
               suggestions: items.slice(0, 12),
               container: (document.querySelector("#fulltextContainer")?.innerText || "").slice(0, 60) };
    });
    console.log("AFTER TYPE", JSON.stringify(after));
  } else {
    const body = await page.evaluate(() => (document.body.innerText || "").replace(/\s+/g, " ").slice(0, 300));
    console.log("BODY", body);
  }
  await ctx.close();
})().catch((e) => { console.log("ERR", String(e).slice(0, 400)); process.exit(1); });
