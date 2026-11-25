// Capture the API call behind "معاملات امروز" (trades) so we can replay it for
// a date range and rebuild the real average cost.
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
  const ctx = await chromium.launchPersistentContext(PROFILE, {
    channel: "chrome", headless: false,
    args: ["--no-first-run", "--no-default-browser-check"],
  });
  const page = ctx.pages()[0] || (await ctx.newPage());
  const calls = [];
  page.on("request", (r) => {
    const u = r.url();
    if (u.includes("api.meibours")) {
      calls.push({ method: r.method(), url: u.replace("https://api.meibours.ir", ""),
                   body: (r.postData() || "").slice(0, 300) });
    }
  });
  page.on("response", async (r) => {
    const u = r.url();
    if (/Trade|Trades|Transactions|Order\//i.test(u) && u.includes("api.meibours")) {
      try {
        const t = await r.text();
        calls.push({ resp: u.replace("https://api.meibours.ir", ""), status: r.status(),
                     body: t.slice(0, 400) });
      } catch (e) {}
    }
  });

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
    for (let i = 0; i < 6; i++) {
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
  if (await loginFormVisible()) { console.log(JSON.stringify({ error: "login" })); await ctx.close(); return; }

  calls.length = 0;
  for (const title of ["معاملات امروز", "سفارشات امروز", "حساب امروز"]) {
    await page.evaluate((lbl) => {
      let el = null;
      document.querySelectorAll("span.lm_title").forEach((x) => {
        if ((x.innerText || "").trim() === lbl) el = x;
      });
      if (el) {
        const t = el.closest("a,div,li") || el;
        ["mousedown", "mouseup", "click"].forEach((e) =>
          t.dispatchEvent(new MouseEvent(e, { bubbles: true, cancelable: true, view: window })));
      }
    }, title);
    await page.waitForTimeout(3000);
  }

  console.log(JSON.stringify(calls.slice(0, 12), null, 1).slice(0, 4000));
  await ctx.close();
})();
