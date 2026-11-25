// Cancel one open order through the panel (the 4th cell of its grid row is حذف).
//   node cancel_order.js <symbol> <qty>
// Prints "cancelled" when the row disappears from the open-orders list.
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
  const symbol = process.argv[2];
  const qty = process.argv[3] || "";
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
  if (await loginFormVisible()) { console.log("login_failed"); await ctx.close(); return; }

  // open the "سفارشات باز" stack so the grid renders
  await page.evaluate(() => {
    let el = null;
    document.querySelectorAll("span.lm_title").forEach((x) => {
      const t = (x.innerText || "").trim();
      if (t === "سفارشات باز" || t === "سفارشات باز ( نماد )") el = x;
    });
    if (el) {
      const t = el.closest("a,div,li") || el;
      ["mousedown", "mouseup", "click"].forEach((e) =>
        t.dispatchEvent(new MouseEvent(e, { bubbles: true, cancelable: true, view: window })));
    }
  });
  await page.waitForTimeout(4000);

  const before = await page.evaluate(async (api) => (await (await fetch(api + "/Order/GetOpenOrder/OpenOrder", { credentials: "include" })).text()), API);

  const hit = await page.evaluate(({ sym, qty }) => {
    window.__openTab = true;
    let target = null;
    document.querySelectorAll("ul.g-row").forEach((r) => {
      if (target) return;
      const t = (r.innerText || "").replace(/\s+/g, " ");
      if (t.indexOf(sym) >= 0 && t.indexOf("خرید") >= 0 && (!qty || t.indexOf(qty) >= 0)) target = r;
    });
    if (!target) return false;
    const cells = target.querySelectorAll("li");
    const del = cells[4] || cells[cells.length - 1];
    if (!del) return false;
    const b = del.getBoundingClientRect();
    window.__cancelAt = { x: b.x + b.width / 2, y: b.y + b.height / 2 };
    return true;
  }, { sym: symbol, qty });

  if (!hit) { console.log("row_not_found"); await ctx.close(); return; }
  const pos = await page.evaluate(() => window.__cancelAt);
  for (const t of ["mousePressed", "mouseReleased"]) {
    await page.mouse.move(pos.x, pos.y).catch(() => {});
    await page.mouse.down().catch(() => {});
    await page.mouse.up().catch(() => {});
    break;
  }
  await page.waitForTimeout(2500);
  // confirm dialog if any
  await page.evaluate(() => {
    let btn = null;
    document.querySelectorAll("button,a,span,div").forEach((x) => {
      if (btn) return;
      if (/^(بله|تایید|تأیید|حذف)$/.test((x.innerText || "").trim()) && x.offsetParent !== null) btn = x;
    });
    if (btn) ["mousedown", "mouseup", "click"].forEach((e) => btn.dispatchEvent(new MouseEvent(e, { bubbles: true, cancelable: true, view: window })));
  });
  await page.waitForTimeout(4000);

  const after = await page.evaluate(async (api) => (await (await fetch(api + "/Order/GetOpenOrder/OpenOrder", { credentials: "include" })).text()), API);
  const still = String(after).includes(symbol);
  console.log(still ? "still_open" : "cancelled");
  await ctx.close();
})();
