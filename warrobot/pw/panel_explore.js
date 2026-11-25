require("./cdp_shared"); // shared logged-in broker browser (Hark fix #2)
// Explore the broker panel stacks to find the asset-status / P&L tab and the
// executed-order prices. Prints the stack titles and the first rows of the
// interesting ones so we can see the exact columns.
//   node panel_explore.js
const { chromium } = require("playwright");
const fs = require("fs");
const path = require("path");
const { execFileSync } = require("child_process");

const BASE = path.join(__dirname, "..");
const PROFILE = path.join(BASE, "state", "pw-profile");
const OCR = path.join(BASE, "ocr", "read2.js");
const PNG = path.join(BASE, "data", "captcha_pw.png");
const CREDS = JSON.parse(fs.readFileSync(path.join(BASE, "state", "credentials.json"), "utf8"));
const OUT = path.join(BASE, "state", "panel_explore.json");

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

  const titles = await page.evaluate(() => {
    const out = [];
    document.querySelectorAll("span.lm_title").forEach((x) => {
      const t = (x.innerText || "").trim();
      if (t) out.push(t);
    });
    return Array.from(new Set(out));
  });

  const result = { titles, stacks: {} };
  const interesting = titles.filter((t) => /دارایی|حساب|صورت|سود|زیان|معاملات|سفارش|سبد|موقعیت/.test(t));
  for (const title of interesting) {
    const opened = await page.evaluate((lbl) => {
      let el = null;
      document.querySelectorAll("span.lm_title").forEach((x) => {
        if ((x.innerText || "").trim() === lbl) el = x;
      });
      if (!el) return false;
      const t = el.closest("a,div,li") || el;
      ["mousedown", "mouseup", "click"].forEach((e) =>
        t.dispatchEvent(new MouseEvent(e, { bubbles: true, cancelable: true, view: window })));
      return true;
    }, title);
    await page.waitForTimeout(2500);
    const dump = await page.evaluate(() => {
      const rows = [];
      document.querySelectorAll("ul.g-row").forEach((r) => {
        const cells = [];
        r.querySelectorAll("li").forEach((li) => cells.push((li.innerText || "").replace(/\s+/g, " ").trim()));
        if (cells.length) rows.push(cells.slice(0, 16));
      });
      const gridHeaders = [];
      document.querySelectorAll("[class*=grid] li, [class*=header] li").forEach((li) => {
        const t = (li.innerText || "").trim();
        if (t && t.length < 22) gridHeaders.push(t);
      });
      return { rows: rows.slice(0, 6), headers: Array.from(new Set(gridHeaders)).slice(0, 25) };
    });
    result.stacks[title] = { opened, ...dump };
    console.log(`\n### ${title} (opened=${opened}) headers=${JSON.stringify(dump.headers)}`);
    dump.rows.forEach((r) => console.log("   ", JSON.stringify(r)));
  }

  fs.writeFileSync(OUT, JSON.stringify(result, null, 1), "utf8");
  await ctx.close();
})();
