require("./cdp_shared"); // shared logged-in broker browser (Hark fix #2)
// Place one buy order through Playwright and report exactly what happened.
//   node order.js <symbol> <qty> [price]
// Exit output is a single JSON line: {ok, submitted, message, qty, price, limit}
const { chromium } = require("playwright");
const fs = require("fs");
const path = require("path");
const { execFileSync } = require("child_process");
const OCR_SCRIPT = path.join(__dirname, "..", "ocr", "read2.js");
const CAPTCHA_PNG = path.join(__dirname, "..", "data", "captcha_pw.png");
const CREDS = JSON.parse(fs.readFileSync(path.join(__dirname, "..", "state", "credentials.json"), "utf8"));

const PROFILE = path.join(__dirname, "..", "state", "pw-profile");

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
  const symbol = process.argv[2] || "آباد";
  const wantQty = parseInt(process.argv[3] || "100000", 10);
  const wantPrice = process.argv[4] ? parseFloat(process.argv[4]) : null;

  const ctx = await chromium.launchPersistentContext(PROFILE, {
    channel: "chrome",
    headless: false,
    args: ["--no-first-run", "--no-default-browser-check"],
  });
  const page = ctx.pages()[0] || (await ctx.newPage());
  const net = [];
  page.on("request", (r) => {
    const u = r.url();
    if (/order|trade|Insert|Submit/i.test(u)) net.push({ m: r.method(), u: u.slice(-80), body: (r.postData() || "").slice(0, 300) });
  });
  page.on("response", async (r) => {
    const u = r.url();
    if (/order|trade|Insert|Submit/i.test(u)) {
      try { net.push({ resp: u.slice(-80), status: r.status(), body: (await r.text()).slice(0, 300) }); } catch (e) {}
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
    for (let i = 0; i < 3; i++) {
      await page.click("#captcha-img").catch(() => {});
      await page.waitForTimeout(2500);
      const src = await page.getAttribute("#captcha-img", "src").catch(() => "");
      code = solveCaptcha(src || "");
      if (code.length === 4) break;
    }
    if (code.length !== 4) {
      console.log(JSON.stringify({ ok: false, reason: "captcha_unreadable" }));
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
    await page.evaluate(() => { if (typeof submitForm === "function") submitForm(); else document.getElementById("main-form")?.click(); });
    await page.waitForTimeout(12000);
    if (await loginFormVisible()) {
      console.log(JSON.stringify({ ok: false, reason: "login_failed", captcha: code }));
      await ctx.close();
      return;
    }
  }
  await page.goto("https://online.meibours.ir/", { waitUntil: "domcontentloaded", timeout: 60000 });
  await page.waitForTimeout(5000);
  await page.evaluate(() => { let el=null; document.querySelectorAll("span.lm_title").forEach(x=>{ if((x.innerText||"").trim()==="ارسال سفارش") el=x; }); if(el){ const t=el.closest("a,div,li")||el; ["mousedown","mouseup","click"].forEach(e=>t.dispatchEvent(new MouseEvent(e,{bubbles:true,cancelable:true,view:window}))); } });
  await page.waitForTimeout(3000);

  // open the watchlist stack so the symbol box gets a width
  await page.evaluate(() => {
    let el = null;
    document.querySelectorAll("span.lm_title").forEach((x) => {
      if ((x.innerText || "").trim() === "دیده بان") el = x;
    });
    if (el) {
      const t = el.closest("a,div,li") || el;
      ["mousedown", "mouseup", "click"].forEach((e) =>
        t.dispatchEvent(new MouseEvent(e, { bubbles: true, cancelable: true, view: window }))
      );
    }
  });
  await page.waitForTimeout(2500);

  const box = page.locator("#txt_search");
  await box.scrollIntoViewIfNeeded().catch(() => {});
  await page.waitForTimeout(500);
  await box.click({ timeout: 15000 }).catch(() => {});
  await box.fill("").catch(() => {});
  await page.keyboard.type(symbol, { delay: 40 });
  await page.waitForTimeout(2500);

  // click the exact result row
  const clicked = await page.evaluate((sym) => {
    let el = null;
    document.querySelectorAll("div,li,span").forEach((x) => {
      if (el) return;
      if ((x.innerText || "").trim() === sym && x.offsetParent !== null) el = x;
    });
    if (!el) return false;
    const t = el.closest("li,a,div") || el;
    ["mousedown", "mouseup", "click"].forEach((e) =>
      t.dispatchEvent(new MouseEvent(e, { bubbles: true, cancelable: true, view: window }))
    );
    return true;
  }, symbol);
  await page.waitForTimeout(3000);

  const bound = await page.evaluate(() => (document.querySelector("#fulltextContainer")?.innerText || "").trim().slice(0, 12));
  const text = await page.evaluate(() => (document.body.innerText || "").replace(/\s+/g, " "));
  const m = text.match(/آستانه\s*حجم[^\d]{0,6}([\d,]+)\s*[-–]\s*([\d,]+)/);
  const limit = m ? parseInt(m[2].replace(/,/g, ""), 10) : 0;
  const qty = limit ? Math.min(wantQty, limit) : wantQty;

  await page.fill("#send_order_txtCount", String(qty)).catch(() => {});
  await page.fill("#send_order_txtPrice", String(wantPrice || "")).catch(() => {});
  await page.waitForTimeout(600);
  const vals = await page.evaluate(() => [
    document.querySelector("#send_order_txtCount")?.value,
    document.querySelector("#send_order_txtPrice")?.value,
  ]);

  let clickedBtn = false;
  try {
    await page.getByRole("link", { name: "خرید" }).first().click({ timeout: 8000 });
    clickedBtn = true;
  } catch (e) {
    clickedBtn = await page.evaluate(() => {
      const a = document.getElementById("send_order_btnBuySell");
      if (!a) return false;
      a.scrollIntoView({ block: "center" });
      a.click();
      return true;
    });
  }
  await page.waitForTimeout(6000);

  const msg = await page.evaluate(() => {
    let out = "";
    document.querySelectorAll("div,span,p").forEach((x) => {
      if (x.children.length) return;
      const t = (x.innerText || "").trim();
      if (/موفق|عدم پذیرش|خطا|کافی|ثبت شد|انجام شد/.test(t) && !/^مجاز$/.test(t) && t.length < 140) out = t;
    });
    return out;
  });
  console.log(JSON.stringify({ ok: /موفق|ثبت شد/.test(msg), bound, qty, vals, clickedBtn, message: msg, net: net.slice(-4) }));
  await ctx.close();
})();
