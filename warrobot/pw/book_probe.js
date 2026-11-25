require("./cdp_shared"); // shared logged-in broker browser (Hark fix #2)
// Probe: bind a symbol on the broker panel and dump the order-book DOM.
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
  } catch (e) { return ""; }
}

async function visible(page, sel) {
  return page.evaluate((s) => {
    const u = document.querySelector(s);
    if (!u) return false;
    const r = u.getBoundingClientRect();
    const st = window.getComputedStyle(u);
    return r.width > 0 && r.height > 0 && st.visibility !== "hidden" && st.display !== "none";
  }, sel);
}

async function setVal(page, sel, val) {
  await page.evaluate(({ sel, val }) => {
    const el = document.querySelector(sel);
    if (!el) return;
    const s = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set;
    s.call(el, String(val));
    el.dispatchEvent(new Event("input", { bubbles: true }));
    el.dispatchEvent(new Event("change", { bubbles: true }));
  }, { sel, val });
}

(async () => {
  const symbol = process.argv[2] || "فولاد";
  const ctx = await chromium.launchPersistentContext(PROFILE, {
    channel: "chrome", headless: false,
    args: ["--no-first-run", "--no-default-browser-check"],
  });
  const page = ctx.pages()[0] || (await ctx.newPage());
  await page.goto("https://online.meibours.ir/", { waitUntil: "domcontentloaded", timeout: 60000 });
  await page.waitForTimeout(3500);

  if (await visible(page, "#username")) {
    let code = "";
    for (let i = 0; i < 4; i++) {
      await page.click("#captcha-img", { force: true }).catch(() => {});
      await page.waitForTimeout(2000);
      code = solve(await page.getAttribute("#captcha-img", "src").catch(() => ""));
      if (code.length === 4) break;
    }
    await setVal(page, "#username", CREDS.user);
    await setVal(page, "#password", CREDS.password);
    await setVal(page, "#captcha", code);
    await page.evaluate(() => {
      if (typeof submitForm === "function") submitForm();
      else document.getElementById("main-form")?.click();
    });
    await page.waitForTimeout(12000);
  }
  console.log("URL", page.url());

  await page.evaluate(() => {
    let el = null;
    document.querySelectorAll("span.lm_title").forEach((x) => {
      if ((x.innerText || "").trim().includes("دیده")) el = x;
    });
    if (el) {
      const t = el.closest("a,div,li") || el;
      ["mousedown", "mouseup", "click"].forEach((e) =>
        t.dispatchEvent(new MouseEvent(e, { bubbles: true, cancelable: true, view: window })));
    }
  });
  await page.waitForTimeout(2500);
  const box = page.locator("#txt_search");
  await box.click({ timeout: 15000 }).catch(() => {});
  await box.fill("").catch(() => {});
  await page.keyboard.type(symbol, { delay: 40 });
  await page.waitForTimeout(2500);
  await page.evaluate((sym) => {
    let el = null;
    document.querySelectorAll("div,li,span").forEach((x) => {
      if (el) return;
      if ((x.innerText || "").trim() === sym && x.offsetParent !== null) el = x;
    });
    if (el) {
      const t = el.closest("li,a,div") || el;
      ["mousedown", "mouseup", "click"].forEach((e) =>
        t.dispatchEvent(new MouseEvent(e, { bubbles: true, cancelable: true, view: window })));
    }
  }, symbol);
  await page.waitForTimeout(3000);

  const dump = await page.evaluate(() => {
    const out = { bound: (document.querySelector("#fulltextContainer")?.innerText || "").trim().slice(0, 20),
                  tables: [], headings: [] };
    document.querySelectorAll("table").forEach((t, i) => {
      const txt = (t.innerText || "").replace(/\s+/g, " ").trim();
      if (txt.length > 20) {
        out.tables.push({ i, id: t.id || "", cls: (t.className || "").slice(0, 50),
                          text: txt.slice(0, 700) });
      }
    });
    document.querySelectorAll("h1,h2,h3,h4,label,span").forEach((e) => {
      const t = (e.innerText || "").replace(/\s+/g, " ").trim();
      if (t && t.length < 40 && /مظنه|سفارش|خرید|فروش|حجم/.test(t)) out.headings.push(t);
    });
    return out;
  });
  console.log(JSON.stringify(dump, null, 1).slice(0, 6000));
  await ctx.close();
})().catch((e) => { console.log(JSON.stringify({ error: String(e).slice(0, 300) })); process.exit(1); });
