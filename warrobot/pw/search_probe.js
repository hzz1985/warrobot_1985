// Probe the symbol search dropdown after opening the watchlist tab.
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

  await page.evaluate(() => {
    const titles = Array.from(document.querySelectorAll("span.lm_title"));
    const t = titles.find((x) => (x.innerText || "").includes("دیده"));
    if (t) {
      const a = t.closest("a,div,li") || t;
      ["mousedown", "mouseup", "click"].forEach((e) =>
        a.dispatchEvent(new MouseEvent(e, { bubbles: true, cancelable: true, view: window })));
    }
  });
  await page.waitForTimeout(2500);

  const box = page.locator("#txt_search");
  console.log("search visible:", await vis("#txt_search"));
  await box.click({ timeout: 15000 }).catch(() => {});
  await box.fill(symbol, { timeout: 15000 }).catch((e) => console.log("fill err", String(e).slice(0,80)));
  await page.waitForTimeout(1200);
  console.log("value:", await page.evaluate(() => document.querySelector("#txt_search")?.value));
  await page.keyboard.press("ArrowDown").catch(() => {});
  await page.waitForTimeout(400);
  await page.keyboard.press("Enter").catch(() => {});
  await page.waitForTimeout(3000);
  console.log("container after enter:", await page.evaluate(() => (document.querySelector("#fulltextContainer")?.innerText||"").slice(0,40)));

  const dump = await page.evaluate(() => {
    const rect = (e) => {
      const r = e.getBoundingClientRect();
      const st = window.getComputedStyle(e);
      return r.width > 0 && r.height > 0 && st.visibility !== "hidden" && st.display !== "none";
    };
    const hits = [];
    document.querySelectorAll("li,div,span,tr,td,a").forEach((x) => {
      const t = (x.innerText || "").replace(/\s+/g, " ").trim();
      if (rect(x) && t && t.length < 50 && t.includes("آباد")) {
        hits.push({ tag: x.tagName, cls: (x.className || "").toString().slice(0, 40), text: t.slice(0, 40) });
      }
    });
    const dds = [];
    document.querySelectorAll("[class*=dropdown],[class*=auto],[class*=suggest],[id*=dropdown],[role=listbox]").forEach((x) => {
      if (rect(x)) dds.push({ cls: (x.className || "").toString().slice(0, 60), id: x.id,
                              text: (x.innerText || "").replace(/\s+/g, " ").slice(0, 120) });
    });
    return { hits: hits.slice(0, 12), dropdowns: dds.slice(0, 8),
             container: (document.querySelector("#fulltextContainer")?.innerText || "").slice(0, 50) };
  });
  console.log(JSON.stringify(dump, null, 1).slice(0, 2500));
  await ctx.close();
})().catch((e) => { console.log("ERR", String(e).slice(0, 300)); process.exit(1); });
