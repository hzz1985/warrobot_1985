require("./cdp_shared"); // shared logged-in broker browser (Hark fix #2)
// Probe: JS-driven typing into the visible symbol search + autocomplete DOM.
const { chromium } = require("playwright");
const path = require("path");
const fs = require("fs");
const { execFileSync } = require("child_process");

const BASE = path.join(__dirname, "..");
const PROFILE = path.join(BASE, "state", "pw-profile");
const CREDS = JSON.parse(fs.readFileSync(path.join(BASE, "state", "credentials.json"), "utf8"));
const OCR = path.join(BASE, "ocr", "read2.js");
const PNG = path.join(BASE, "data", "captcha_pw.png");

function solve(uri) {
  if (!uri || !uri.startsWith("data:image")) return "";
  fs.writeFileSync(PNG, Buffer.from(uri.split(",")[1], "base64"));
  try {
    const out = execFileSync(process.execPath, [OCR, PNG], { encoding: "utf8", timeout: 180000 });
    return String(JSON.parse((out || "{}").trim() || "{}").text || "");
  } catch { return ""; }
}

(async () => {
  const symbol = process.argv[2] || "آبادا";
  const ctx = await chromium.launchPersistentContext(PROFILE, {
    channel: "chrome", headless: false,
    args: ["--no-first-run", "--no-default-browser-check"],
  });
  const page = ctx.pages()[0] || (await ctx.newPage());
  await page.goto("https://online.meibours.ir/", { waitUntil: "domcontentloaded", timeout: 60000 });
  await page.waitForTimeout(3500);

  const isVis = async (sel) => page.evaluate((s) => {
    const all = Array.from(document.querySelectorAll(s));
    return all.some((u) => {
      const r = u.getBoundingClientRect();
      const st = window.getComputedStyle(u);
      return r.width > 0 && r.height > 0 && st.visibility !== "hidden" && st.display !== "none";
    });
  }, sel);

  if (await isVis("#username")) {
    let code = "";
    for (let i = 0; i < 4; i++) {
      await page.click("#captcha-img", { force: true }).catch(() => {});
      await page.waitForTimeout(2000);
      code = solve(await page.getAttribute("#captcha-img", "src").catch(() => ""));
      if (code.length === 4) break;
    }
    await page.evaluate(([u, p, c]) => {
      const set = (sel, v) => {
        const el = document.querySelector(sel); if (!el) return;
        const d = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set;
        d.call(el, v); el.dispatchEvent(new Event("input", { bubbles: true }));
        el.dispatchEvent(new Event("change", { bubbles: true }));
      };
      set("#username", u); set("#password", p); set("#captcha", c);
      if (typeof submitForm === "function") submitForm();
      else document.getElementById("main-form")?.click();
    }, [CREDS.user, CREDS.password, code]);
    await page.waitForTimeout(12000);
  }

  // open the watchlist tab
  await page.evaluate(() => {
    const t = Array.from(document.querySelectorAll("span.lm_title"))
      .find((x) => (x.innerText || "").includes("دیده"));
    if (t) {
      const a = t.closest("a,div,li") || t;
      ["mousedown", "mouseup", "click"].forEach((e) =>
        a.dispatchEvent(new MouseEvent(e, { bubbles: true, cancelable: true, view: window })));
    }
  });
  await page.waitForTimeout(2500);

  // JS typing: set value + dispatch keyboard-ish events, then read the dropdown
  const box2 = page.locator("#txt_search:visible").first();
  await box2.click({ timeout: 15000 }).catch(() => {});
  await box2.fill("").catch(() => {});
  await page.keyboard.type(symbol, { delay: 90 });
  await page.waitForTimeout(1200);
  const res = await page.evaluate(() => {
    const el = Array.from(document.querySelectorAll("#txt_search")).find((x) => x.getBoundingClientRect().width > 0);
    return { value: el ? el.value : null };
  });
  console.log("TYPE", JSON.stringify(res));
  await page.waitForTimeout(2500);

  const dd = await page.evaluate(() => {
    const rect = (e) => {
      const r = e.getBoundingClientRect();
      const st = window.getComputedStyle(e);
      return r.width > 0 && r.height > 0 && st.visibility !== "hidden" && st.display !== "none";
    };
    const ac = Array.from(document.querySelectorAll("li,div,span,td"))
      .filter((x) => rect(x) && !x.closest("[id^=row_]") && (x.innerText || "").length < 45)
      .map((x) => ({ tag: x.tagName, cls: (x.className||"").toString().slice(0,30),
                     text: (x.innerText || "").replace(/\s+/g, " ").slice(0, 60) }))
      .filter((x) => x.text.includes("آبادا") || x.text.includes("آ.ب"));
    const container = Array.from(document.querySelectorAll("#fulltextContainer"))
      .filter(rect).map((x) => (x.innerText || "").trim().slice(0, 30));
    return { autocomplete: ac.slice(0, 6), containers: container,
             searchVal: Array.from(document.querySelectorAll("#txt_search")).map((x) => x.value) };
  });
  console.log(JSON.stringify(dd, null, 1).slice(0, 2000));
  await ctx.close();
})().catch((e) => { console.log("ERR", String(e).slice(0, 300)); process.exit(1); });
