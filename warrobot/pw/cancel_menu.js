require("./cdp_shared"); // shared logged-in broker browser (Hark fix #2)
// Cancel an order via the row context menu (the panel's real cancel path).
//   node cancel_menu.js <symbol>
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
  const symbol = process.argv[2] || "";
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

  // open the open-orders stack
  await page.evaluate(() => {
    let el = null;
    document.querySelectorAll("span.lm_title").forEach((x) => {
      if (((x.innerText || "").trim()).indexOf("سفارشات باز") === 0) el = x;
    });
    if (el) {
      const t = el.closest("a,div,li") || el;
      ["mousedown", "mouseup", "click"].forEach((e) =>
        t.dispatchEvent(new MouseEvent(e, { bubbles: true, cancelable: true, view: window })));
    }
  });
  await page.waitForTimeout(5000);

  // click the row's context-menu trigger
  const opened = await page.evaluate((sym) => {
    let row = null;
    document.querySelectorAll("ul.g-row").forEach((r) => {
      if (row) return;
      if ((r.innerText || "").indexOf(sym) >= 0) row = r;
    });
    if (!row) return false;
    const more = row.querySelector(".more");
    if (!more) return false;
    const b = more.getBoundingClientRect();
    window.__ctx = { x: b.x + b.width / 2, y: b.y + b.height / 2 };
    ["mousedown", "mouseup", "click"].forEach((e) =>
      more.dispatchEvent(new MouseEvent(e, { bubbles: true, cancelable: true, view: window })));
    return true;
  }, symbol);
  if (!opened) { console.log(JSON.stringify({ ok: false, error: "row_or_menu_not_found" })); await ctx.close(); return; }
  await page.waitForTimeout(1500);

  // inspect the context menu that appeared
  const menu = await page.evaluate(() => {
    const items = [];
    document.querySelectorAll("div,li,a,span").forEach((x) => {
      const t = (x.innerText || "").trim();
      if (!t || t.length > 40 || x.children.length > 2) return;
      if (/حذف|لغو|ابطال|Cancel|Delete/i.test(t)) {
        const r = x.getBoundingClientRect();
        if (r.width > 0 && r.height > 0) items.push({ t, x: r.x, y: r.y });
      }
    });
    return items.slice(0, 8);
  });

  if (!menu.length) { console.log(JSON.stringify({ ok: false, error: "no_menu_items", opened })); await ctx.close(); return; }

  const target = menu[0];
  await page.mouse.click(target.x + 5, target.y + 5);
  await page.waitForTimeout(2500);
  // confirm dialog if any
  await page.evaluate(() => {
    let btn = null;
    document.querySelectorAll("button,a,span,div").forEach((x) => {
      if (btn) return;
      if (/^(بله|تایید|تأیید|حذف|لغو سفارش)$/.test((x.innerText || "").trim()) && x.offsetParent !== null) btn = x;
    });
    if (btn) ["mousedown", "mouseup", "click"].forEach((e) =>
      btn.dispatchEvent(new MouseEvent(e, { bubbles: true, cancelable: true, view: window })));
  });
  await page.waitForTimeout(4000);

  // verify
  const check = await page.evaluate(async () => {
    const r = await fetch("https://api.meibours.ir/Web/V1/Order/GetOpenOrder/OpenOrder", { credentials: "include" });
    return (await r.text()).slice(0, 300);
  });
  console.log(JSON.stringify({ ok: true, clicked: target.t, after: check }).slice(0, 700));
  await ctx.close();
})();
