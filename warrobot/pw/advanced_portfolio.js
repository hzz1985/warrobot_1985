// Open "پرتفوی لحظه ای پیشرفته" (advanced realtime portfolio) and read its grid —
// it usually exposes the real average cost and the P/L per position.
//   node advanced_portfolio.js
const { chromium } = require("playwright");
const fs = require("fs");
const path = require("path");
const { execFileSync } = require("child_process");

const BASE = path.join(__dirname, "..");
const PROFILE = path.join(BASE, "state", "pw-profile");
const OCR = path.join(BASE, "ocr", "read2.js");
const PNG = path.join(BASE, "data", "captcha_pw.png");
const CREDS = JSON.parse(fs.readFileSync(path.join(BASE, "state", "credentials.json"), "utf8"));
const OUT = path.join(BASE, "state", "advanced_portfolio.json");

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
    channel: "chrome", headless: true,
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

  const clicked = await page.evaluate(() => {
    const a = document.querySelector('a[href="RealtimePortfolioAdvanced"]');
    if (!a) return false;
    ["mousedown", "mouseup", "click"].forEach((e) =>
      a.dispatchEvent(new MouseEvent(e, { bubbles: true, cancelable: true, view: window })));
    return true;
  });
  await page.waitForTimeout(7000);

  const dump = await page.evaluate(() => {
    const headers = [];
    document.querySelectorAll("li,th").forEach((x) => {
      if (x.children.length) return;
      const t = (x.innerText || "").replace(/\s+/g, " ").trim();
      if (t && t.length < 24) headers.push(t);
    });
    const rows = [];
    document.querySelectorAll("ul.g-row, tr").forEach((r) => {
      const cells = [];
      r.querySelectorAll("li,td").forEach((li) => cells.push((li.innerText || "").replace(/\s+/g, " ").trim()));
      if (cells.length >= 3) rows.push(cells.slice(0, 20));
    });
    return { url: location.href, headers: Array.from(new Set(headers)).slice(0, 40), rows: rows.slice(0, 12) };
  });

  console.log(JSON.stringify({ clicked, ...dump }, null, 1).slice(0, 4000));
  fs.writeFileSync(OUT, JSON.stringify({ clicked, ...dump }, null, 1), "utf8");
  await ctx.close();
})();
