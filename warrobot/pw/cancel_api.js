require("./cdp_shared"); // shared logged-in broker browser (Hark fix #2)
// Cancel one order through the broker API (tries the plausible endpoints).
//   node cancel_api.js <orderid> [symbol]
// Prints one JSON line: {"ok":bool,"endpoint":...,"response":...}
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
  const orderId = process.argv[2] || "";
  const symbol = process.argv[3] || "";
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
  if (await loginFormVisible()) { console.log(JSON.stringify({ ok: false, error: "login" })); await ctx.close(); return; }

  const out = await page.evaluate(async ({ api, orderId, symbol }) => {
    const tries = [
      { url: api + "/Order/Cancel", body: { orderId: Number(orderId) || orderId } },
      { url: api + "/Order/DeleteOrder", body: { orderId: Number(orderId) || orderId } },
      { url: api + "/Order/CancelOrder", body: { orderId: Number(orderId) || orderId } },
      { url: api + "/Order/Delete", body: { OrderId: Number(orderId) || orderId } },
    ];
    const results = [];
    for (const t of tries) {
      try {
        const r = await fetch(t.url, {
          method: "POST", credentials: "include",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(t.body),
        });
        const txt = (await r.text()).slice(0, 300);
        results.push({ url: t.url.split("/").pop(), status: r.status, txt });
        if (r.status === 200 && /IsSuccessfull":true/.test(txt) && !/"Data":null/.test(txt)) {
          return { ok: true, endpoint: t.url, response: txt, results };
        }
      } catch (e) {
        results.push({ url: t.url, error: String(e).slice(0, 80) });
      }
    }
    return { ok: false, results };
  }, { api: API, orderId, symbol });

  console.log(JSON.stringify(out).slice(0, 1500));
  await ctx.close();
})();
