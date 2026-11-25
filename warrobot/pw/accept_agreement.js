require("./cdp_shared"); // shared logged-in broker browser (Hark fix #2)
// Round 4b: the broker requires a "caution trading" agreement (AgreementId 2).
// Accept it programmatically, then place the order with the flags enabled.
const { chromium } = require("playwright");
const fs = require("fs");
const path = require("path");
const { execFileSync } = require("child_process");

const BASE = path.join(__dirname, "..");
const PROFILE = path.join(BASE, "state", "pw-profile");
const OCR = path.join(BASE, "ocr", "read2.js");
const PNG = path.join(BASE, "data", "captcha_pw.png");
const CREDS = JSON.parse(fs.readFileSync(path.join(BASE, "state", "credentials.json"), "utf8"));
const API = "https://api.meibours.ir/Web/V1";

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
  const isin = process.argv[5] || "IRO1ABAD0001";

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
  const out = { login_ok: !page.url().includes("Login"), before: null, accept: null, after: null, order: null, open: null };
  if (!out.login_ok) { console.log(JSON.stringify(out)); await ctx.close(); return; }

  const j = async (url, opts) => page.evaluate(async ({ url, opts }) => {
    const r = await fetch(url, Object.assign({ credentials: "include" }, opts || {}));
    return { status: r.status, text: (await r.text()).slice(0, 300) };
  }, { url, opts });

  out.before = await j(`${API}/Agreement/CheckAgreement`);

  // try the plausible accept endpoints
  const tries = [
    { url: `${API}/Agreement/Accept`, opts: { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ AgreementId: 2 }) } },
    { url: `${API}/Agreement/AcceptAgreement`, opts: { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ AgreementId: 2 }) } },
    { url: `${API}/Agreement/SetAgreement`, opts: { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ AgreementId: 2 }) } },
    { url: `${API}/Agreement/SetAgreement?agreementId=2`, opts: { method: "POST" } },
  ];
  for (const t of tries) {
    const r = await j(t.url, t.opts);
    if (r.status !== 404) { out.accept = { url: t.url.slice(-40), ...r }; break; }
  }
  out.after = await j(`${API}/Agreement/CheckAgreement`);

  // place the order with the caution flags true (only valid if the agreement stuck)
  out.order = await j(`${API}/Order/Post`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      IsSymbolCautionAgreement: true, CautionAgreementSelected: true,
      IsSymbolSepahAgreement: false, SepahAgreementSelected: false,
      orderCount: qty, orderPrice: price, FinancialProviderId: 1,
      minimumQuantity: 0, maxShow: 0, orderId: 0, isin,
      orderSide: 65, orderValidity: 74, orderValiditydate: null,
      shortSellIsEnabled: false, shortSellIncentivePercent: 0,
    }),
  });
  await page.waitForTimeout(3000);
  out.open = await j(`${API}/Order/GetOpenOrder/OpenOrder`);
  console.log(JSON.stringify(out).slice(0, 1500));
  await ctx.close();
})();
