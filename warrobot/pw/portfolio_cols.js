require("./cdp_shared"); // shared logged-in broker browser (Hark fix #2)
// Dump the portfolio grid header cell list + one full row so column order is known.
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

  const info = await page.evaluate(() => {
    const out = { headerCells: [], rowCells: [], ariaLabels: [] };
    // header: look for the element that carries the column titles
    document.querySelectorAll("li,th,div").forEach((x) => {
      const t = (x.innerText || "").trim();
      if (t.length > 1 && t.length < 20 && x.children.length === 0) out.ariaLabels.push(t);
    });
    const row = document.querySelector("ul.g-row");
    if (row) {
      row.querySelectorAll("li").forEach((li, i) => {
        out.rowCells.push({ i, id: li.id || "", w: Math.round(li.getBoundingClientRect().width),
                            text: (li.innerText || "").replace(/\s+/g, " ").trim().slice(0, 40) });
      });
    }
    // the grid header often lives in .g-head or [class*=header]
    const head = document.querySelector(".g-head, [class*=head]");
    if (head) out.headerText = (head.innerText || "").replace(/\s+/g, " ").slice(0, 300);
    return out;
  });
  console.log(JSON.stringify({ header: info.headerText, cells: info.rowCells }).slice(0, 3000));
  await ctx.close();
})();
