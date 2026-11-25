// Dump the open-orders panel markup so the cancel button can be located.
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
  if (await loginFormVisible()) { console.log("login failed"); await ctx.close(); return; }

  // click the open-orders stack
  await page.evaluate(() => {
    let el = null;
    document.querySelectorAll("span.lm_title").forEach((x) => {
      const t = (x.innerText || "").trim();
      if (t.indexOf("سفارشات باز") === 0) el = x;
    });
    if (el) {
      const t = el.closest("a,div,li") || el;
      ["mousedown", "mouseup", "click"].forEach((e) =>
        t.dispatchEvent(new MouseEvent(e, { bubbles: true, cancelable: true, view: window })));
    }
  });
  await page.waitForTimeout(5000);

  const info = await page.evaluate(() => {
    const out = { grids: [], rows: 0, sample: "" };
    document.querySelectorAll("[class*=grid],[id*=grid]").forEach((g) => {
      out.grids.push({ id: g.id, cls: g.className, rows: g.querySelectorAll("tr,li").length });
    });
    const trs = document.querySelectorAll("tr");
    out.rows = trs.length;
    for (const tr of trs) {
      if ((tr.innerText || "").indexOf("آواپارس") >= 0) {
        out.sample = tr.outerHTML.slice(0, 1500);
        break;
      }
    }
    if (!out.sample) {
      for (const ul of document.querySelectorAll("ul")) {
        if ((ul.innerText || "").indexOf("آواپارس") >= 0) {
          out.sample = ul.outerHTML.slice(0, 1500);
          break;
        }
      }
    }
    if (!out.sample) {
      const t = document.body.innerHTML;
      const i = t.indexOf("آواپارس");
      out.sample = i >= 0 ? t.slice(Math.max(0, i - 800), i + 800) : "symbol not in DOM";
    }
    return out;
  });
  console.log(JSON.stringify(info).slice(0, 2600));
  await ctx.close();
})();
