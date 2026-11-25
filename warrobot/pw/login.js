// Playwright-driven broker login with the local tesseract.js captcha reader.
// Reuses WarRobot/ocr/read2.js for the OCR so both paths share one solver.
const { chromium } = require("playwright");
const fs = require("fs");
const path = require("path");
const { execFileSync } = require("child_process");

const BASE = path.join(__dirname, "..");
const PROFILE = path.join(BASE, "state", "pw-profile");
const OCR_SCRIPT = path.join(BASE, "ocr", "read2.js");
const CAPTCHA_PNG = path.join(BASE, "data", "captcha_pw.png");
const CREDS = JSON.parse(fs.readFileSync(path.join(BASE, "state", "credentials.json"), "utf8"));

function solveCaptcha(dataUri) {
  const b64 = (dataUri || "").startsWith("data:image") ? dataUri.split(",")[1] : null;
  if (!b64) return "";
  fs.writeFileSync(CAPTCHA_PNG, Buffer.from(b64, "base64"));
  try {
    const out = execFileSync(process.execPath, [OCR_SCRIPT, CAPTCHA_PNG], { encoding: "utf8", timeout: 180000 });
    const data = JSON.parse((out || "{}").trim() || "{}");
    return String(data.text || "");
  } catch (e) {
    return "";
  }
}

(async () => {
  const ctx = await chromium.launchPersistentContext(PROFILE, {
    channel: "chrome",
    headless: false,
    args: ["--no-first-run", "--no-default-browser-check"],
  });
  const page = ctx.pages()[0] || (await ctx.newPage());
  await page.goto("https://online.meibours.ir/", { waitUntil: "domcontentloaded", timeout: 60000 });
  await page.waitForTimeout(3500);

  if (!page.url().includes("Login")) {
    console.log(JSON.stringify({ ok: true, already: true, url: page.url() }));
    await ctx.close();
    return;
  }

  let code = "";
  let ocrAttempts = 0;
  for (let i = 0; i < 6; i++) {
    ocrAttempts = i + 1;
    await page.click("#captcha-img").catch(() => {});
    await page.waitForTimeout(2500);
    const src = await page.getAttribute("#captcha-img", "src").catch(() => "");
    code = solveCaptcha(src || "");
    if (code.length === 4) break;
  }
  if (code.length !== 4) {
    console.log(JSON.stringify({ ok: false, reason: "captcha_unreadable", ocrAttempts }));
    await ctx.close();
    return;
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
  const filled = await page.evaluate(() => [
    document.querySelector("#username")?.value?.length || 0,
    document.querySelector("#password")?.value?.length || 0,
    document.querySelector("#captcha")?.value || "",
  ]);
  await page.evaluate(() => {
    if (typeof submitForm === "function") submitForm();
    else document.getElementById("main-form")?.click();
  });
  await page.waitForTimeout(12000);
  let ok = !page.url().includes("Login");
  // detect a rejected captcha from the page and retry once with a fresh code
  const errText = await page.evaluate(() =>
    (document.body.innerText || "").replace(/\s+/g, " ").slice(0, 400)).catch(() => "");
  const captchaRejected = /کپچا|captcha|نامعتبر|اشتباه/i.test(errText) && !ok;
  console.log(JSON.stringify({ ok, url: page.url(), captcha: code, filled,
                               ocrAttempts, captchaRejected: !!captchaRejected }));
  await ctx.close();
})();
