require("./cdp_shared"); // shared logged-in broker browser (Hark fix #2)
// Read the symbol's trading state as the panel shows it (before ordering).
//   node symbol_state.js <symbol>
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
  const ctx = await chromium.launchPersistentContext(PROFILE, {
    channel: "chrome", headless: false,
    args: ["--no-first-run", "--no-default-browser-check"],
  });
  const page = ctx.pages()[0] || (await ctx.newPage());
  await page.goto("https://online.meibours.ir/", { waitUntil: "domcontentloaded", timeout: 60000 });
  await page.waitForTimeout(3500);
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
    let code = "";
    for (let i = 0; i < 3; i++) {
      await page.click("#captcha-img").catch(() => {});
      await page.waitForTimeout(2500);
      code = solve(await page.getAttribute("#captcha-img", "src").catch(() => ""));
      if (code.length === 4) break;
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
    }, { u: CREDS.user, p: CREDS.password, c: code });
    await page.evaluate(() => { if (typeof submitForm === "function") submitForm(); else document.getElementById("main-form")?.click(); });
    await page.waitForTimeout(12000);
  }
  if (await loginFormVisible()) { console.log(JSON.stringify({ ok: false, reason: "login" })); await ctx.close(); return; }

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
  await page.waitForTimeout(3500);

  const info = await page.evaluate(() => {
    const states = [];
    document.querySelectorAll(".symbol-state, [class*=symbol-state]").forEach((x) => states.push({ t: (x.innerText || "").trim(), title: x.getAttribute("title") || "" }));
    const t = (document.body.innerText || "").replace(/\s+/g, " ");
    const g = (re) => { const m = t.match(re); return m ? m[1] : ""; };
    return {
      states,
      bound: (document.querySelector("#fulltextContainer")?.innerText || "").trim().slice(0, 12),
      astaneh: g(/آستانه\s*حجم[^\d]{0,6}([\d,]+)\s*[-–]\s*([\d,]+)/),
      last: g(/آخرین معامله\s*([\d,]+)/),
      closing: g(/قیمت پایانی\s*\(?%?\)?\s*([\d,]+)/),
      buyBtn: !!document.getElementById("send_order_btnBuySell"),
      priceField: document.querySelector("#send_order_txtPrice")?.value || "",
      countField: document.querySelector("#send_order_txtCount")?.value || "",
    };
  });
  console.log(JSON.stringify(info));
  await ctx.close();
})();
