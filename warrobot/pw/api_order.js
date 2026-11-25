// Round 3b: call the order API directly (fetch inside the logged-in page),
// exactly the payload the panel sends. Deterministic: no UI click involved.
//   node api_order.js <symbol> <qty> <price>
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
  const symbol = process.argv[2] || "آباد";
  const qty = parseInt(process.argv[3] || "10000", 10);
  const price = parseFloat(process.argv[4] || "0");

  const ctx = await chromium.launchPersistentContext(PROFILE, {
    channel: "chrome", headless: false,
    args: ["--no-first-run", "--no-default-browser-check"],
  });
  const page = ctx.pages()[0] || (await ctx.newPage());
  const out = { symbol, qty, price, captcha: "", isin: "", posted: null, open: null };

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
    out.captcha = code;
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
  if (await loginFormVisible()) { console.log(JSON.stringify(out)); await ctx.close(); return; }
  await page.waitForTimeout(2000);

  // bind the symbol so its isin is known
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
  await page.waitForTimeout(3000);
  out.isin = process.argv[5] || out.isin;

  out.isin = process.argv[5] || await page.evaluate(() => {
    const m = (document.body.innerText || "").match(/IRO\d?[A-Z0-9]{8,}/);
    return m ? m[0] : "";
  });

  await page.evaluate((v) => { window.__isinArg = v; }, out.isin);
  out.posted = await page.evaluate(async ({ qty, price }) => {
    const body = {
      IsSymbolCautionAgreement: false, CautionAgreementSelected: false,
      IsSymbolSepahAgreement: false, SepahAgreementSelected: false,
      orderCount: qty, orderPrice: price, FinancialProviderId: 1,
      minimumQuantity: 0, maxShow: 0, orderId: 0, isin: window.__isinArg,
      orderSide: 65, orderValidity: 74, orderValiditydate: null,
      shortSellIsEnabled: false, shortSellIncentivePercent: 0,
    };
    const r = await fetch("https://api.meibours.ir/Web/V1/Order/Post", {
      method: "POST", credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    return { status: r.status, text: (await r.text()).slice(0, 400) };
  }, { qty, price });

  await page.waitForTimeout(4000);
  out.open = await page.evaluate(async () => {
    const r = await fetch("https://api.meibours.ir/Web/V1/Order/GetOpenOrder/OpenOrder", { credentials: "include" });
    return (await r.text()).slice(0, 400);
  });
  console.log(JSON.stringify(out).slice(0, 1600));
  await ctx.close();
})();
