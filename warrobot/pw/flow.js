require("./cdp_shared"); // shared logged-in broker browser (Hark fix #2)
/**
 * Order flow: bind a symbol, type quantity/price with read-back verification,
 * click buy/sell, then prove what the broker actually recorded.
 *
 *   node flow.js <symbol> <isin> <qty> <price> [scope] [side]
 *        side: "sell" for a sell ticket, otherwise buy
 *        FLOW_DRY=1 -> everything except the final click (rehearsal)
 *
 * Output JSON (stdout):
 *   bound, limit, qty, intended_price, price_ok, count_ok, dry,
 *   placed_price, placed_orderid, mismatch, mismatch_fixed,
 *   submitted, message, url
 */
const fs = require("fs"), path = require("path"), { execFileSync } = require("child_process");
const { chromium } = require("playwright");
const SESSION = require("./session");

const BASE = path.join(__dirname, "..");
const PROFILE = path.join(BASE, "state", "pw-profile");
const CREDS = JSON.parse(fs.readFileSync(path.join(BASE, "state", "credentials.json"), "utf8"));
const OCR = path.join(BASE, "ocr", "read2.js");
const PNG = path.join(BASE, "data", "captcha_pw.png");
const API = "https://api.meibours.ir/Web/V1";

function solve(uri) {
  if (!uri || !uri.startsWith("data:image")) return "";
  fs.writeFileSync(PNG, Buffer.from(uri.split(",")[1], "base64"));
  try {
    const out = execFileSync(process.execPath, [OCR, PNG], { encoding: "utf8", timeout: 180000 });
    return String(JSON.parse((out || "{}").trim() || "{}").text || "");
  } catch (e) { return ""; }
}

(async () => {
  const symbol = process.argv[2] || "";
  const isin = process.argv[3] || "";
  const wantQty = parseInt(process.argv[4] || "0", 10);
  const wantPrice = process.argv[5] ? parseFloat(process.argv[5]) : 0;
  const side = String(process.argv[7] || process.argv[6] || "buy").toLowerCase() === "sell"
    ? "sell" : "buy";

  const out = { url: "", bound: "", limit: 0, qty: 0, intended_price: wantPrice || null,
                price_ok: false, count_ok: false, placed_price: null,
                placed_orderid: null, mismatch: null, mismatch_fixed: null,
                submitted: false, message: "", side, calls: [] };

  const ctx = await chromium.launchPersistentContext(PROFILE, {
    channel: "chrome", headless: false,
    args: ["--no-first-run", "--no-default-browser-check"],
  });
  await SESSION.load(ctx);
  const page = ctx.pages()[0] || await ctx.newPage();

  page.on("response", async (r) => {
    const u = r.url();
    if (/Order\/(Post|Delete)|Agreement/i.test(u)) {
      try {
        out.calls.push({ req: u.split("?")[0].split("/").slice(-2).join("/"),
                         status: r.status(), body: (await r.text()).slice(0, 400) });
      } catch (e) {}
    }
  });

  function visibleEl(sel) {
    const all = Array.from(document.querySelectorAll(sel));
    return all.find((x) => {
      const r = x.getBoundingClientRect();
      const st = window.getComputedStyle(x);
      return r.width > 0 && r.height > 0 && st.visibility !== "hidden" && st.display !== "none";
    }) || all[0] || null;
  }
  async function setVal(sel, val) {
    return await page.evaluate(({ sel, val }) => {
      const visibleEl = (s) => {
        const all = Array.from(document.querySelectorAll(s));
        return all.find((x) => {
          const r = x.getBoundingClientRect();
          const st = window.getComputedStyle(x);
          return r.width > 0 && r.height > 0 && st.visibility !== "hidden" && st.display !== "none";
        }) || all[0] || null;
      };
      const el = visibleEl(sel);
      if (!el) return false;
      const proto = el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype
                                                      : HTMLInputElement.prototype;
      Object.getOwnPropertyDescriptor(proto, "value").set.call(el, String(val));
      el.dispatchEvent(new Event("input", { bubbles: true }));
      el.dispatchEvent(new Event("change", { bubbles: true }));
      return true;
    }, { sel, val });
  }
  async function readVal(sel) {
    return await page.evaluate((sel) => {
      const all = Array.from(document.querySelectorAll(sel));
      const el = all.find((x) => {
        const r = x.getBoundingClientRect();
        const st = window.getComputedStyle(x);
        return r.width > 0 && r.height > 0 && st.visibility !== "hidden" && st.display !== "none";
      }) || all[0];
      return el ? String(el.value || "") : "";
    }, sel);
  }
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

  await page.goto("https://online.meibours.ir/", { waitUntil: "domcontentloaded", timeout: 60000 });
  await page.waitForTimeout(3500);
  if (await SESSION.needLogin(page)) {
  const _gate = SESSION.loginAllowed();
  if (!_gate.ok) {
    console.log(JSON.stringify({ message: "login_blocked", reason: _gate.reason, day: _gate.day }));
    await ctx.close();
    return;
  }
  SESSION.noteLogin();
    let code = "";
    for (let i = 0; i < 4; i++) {
      await page.click("#captcha-img", { force: true }).catch(() => {});
      await page.waitForTimeout(2000);
      code = solve(await page.getAttribute("#captcha-img", "src").catch(() => ""));
      if (code.length === 4) break;
    }
    await setVal("#username", CREDS.user);
    await setVal("#password", CREDS.password);
    await setVal("#captcha", code);
    await page.evaluate(() => {
      if (typeof submitForm === "function") submitForm();
      else document.getElementById("main-form")?.click();
    });
    await page.waitForTimeout(12000);
  }
  out.url = page.url();
  if (/login/i.test(out.url)) {
    out.message = "login_failed";
    console.log(JSON.stringify(out));
    await SESSION.save(ctx).catch(()=>{});
 await ctx.close();
    return;
  }

  // ---- open the watchlist tab (normalized match), then bind ---------------
  const tabNorm = (t) => String(t || "")
    .replace(/[\u200c\u200f\u200e\u00a0]/g, "")
    .replace(/[ي]/g, "ی").replace(/[ك]/g, "ک")
    .replace(/\s+/g, " ").trim();

  async function openWatchTab() {
    const res = await page.evaluate(() => {
      const norm = (t) => String(t || "")
        .replace(/[\u200c\u200f\u200e\u00a0]/g, "")
        .replace(/[ي]/g, "ی").replace(/[ك]/g, "ک")
        .replace(/\s+/g, " ").trim();
      const titles = Array.from(document.querySelectorAll("span.lm_title"));
      const names = titles.map((x) => norm(x.innerText));
      const wanted = ["دیده بان", "دیده‌بان", "ديده بان"];
      const target = titles.find((x) => wanted.includes(norm(x.innerText))) ||
                     titles.find((x) => norm(x.innerText).includes("دیده"));
      if (!target) return { ok: false, names: names.slice(0, 8) };
      const t = target.closest("a,div,li") || target;
      ["mousedown", "mouseup", "click"].forEach((e) =>
        t.dispatchEvent(new MouseEvent(e, { bubbles: true, cancelable: true, view: window })));
      return { ok: true, title: norm(target.innerText) };
    });
    out.tab_open = res;
    await page.waitForTimeout(2500);
  }

  async function searchVisible() {
    return await page.evaluate(() => {
      const el = document.querySelector("#txt_search");
      if (!el) return false;
      const r = el.getBoundingClientRect();
      const st = window.getComputedStyle(el);
      return r.width > 0 && r.height > 0 && st.visibility !== "hidden" && st.display !== "none";
    });
  }

  for (let t = 0; t < 3 && !(await searchVisible()); t++) {
    await openWatchTab();
    await page.waitForTimeout(1200);
  }
  if (!(await searchVisible())) {
    out.message = "watchlist_tab_not_opened";
    console.log(JSON.stringify(out));
    await ctx.close();
    return;
  }

  // ---- bind: type -> pick the suggestion whose base name matches ------------
  const norm = (t) => String(t || "")
    .replace(/[\u200c\u200f\u200e\u00a0]/g, "")
    .replace(/[ي]/g, "ی").replace(/[ك]/g, "ک")
    .replace(/\s+/g, " ").trim();

  async function typeSymbol() {
    const box2 = page.locator("#txt_search:visible").first();
    await box2.scrollIntoViewIfNeeded().catch(() => {});
    await box2.click({ timeout: 15000 }).catch(() => {});
    await box2.fill("").catch(() => {});
    await page.keyboard.type(symbol, { delay: 90 });
    await page.waitForTimeout(2200);
  }

  async function clickSuggestion() {
    return await page.evaluate((sym) => {
      const norm = (t) => String(t || "")
    .replace(/[\u200c\u200f\u200e\u00a0]/g, "")
    .replace(/[ي]/g, "ی").replace(/[ك]/g, "ک")
    .replace(/\s+/g, " ").trim();
      const want = norm(sym);
      const vis = (e) => {
        const r = e.getBoundingClientRect();
        return r.width > 0 && r.height > 0;
      };
      const items = [];
      document.querySelectorAll("div,li,span").forEach((e) => {
        if (!vis(e)) return;
        if (e.closest("[id^=row_]")) return;
        const t = norm(e.innerText);
        if (!t || t.length > 60) return;
        if (!t.startsWith(want)) return;
        items.push({ e, t });
      });
      // prefer "نام - شرح" items, then the shortest exact prefix
      const pick = items.find((x) => x.t.includes(" - ")) || items[0];
      if (!pick) return { clicked: false, seen: [] };
      const node = pick.e.closest("li,div") || pick.e;
      ["mousedown", "mouseup", "click"].forEach((ev) =>
        node.dispatchEvent(new MouseEvent(ev, { bubbles: true, cancelable: true, view: window })));
      return { clicked: true, matched: pick.t.slice(0, 40) };
    }, symbol);
  }

  async function gridFallback() {
    return await page.evaluate((sym) => {
      const norm = (t) => String(t || "")
    .replace(/[\u200c\u200f\u200e\u00a0]/g, "")
    .replace(/[ي]/g, "ی").replace(/[ك]/g, "ک")
    .replace(/\s+/g, " ").trim();
      const want = norm(sym);
      const rows = Array.from(document.querySelectorAll("[id^=row_]")).filter((r) => {
        const b = r.getBoundingClientRect();
        return b.width > 0 && b.height > 0;
      });
      const el = rows.find((r) => norm(r.innerText).startsWith(want));
      if (!el) return { clicked: false, rows: rows.length };
      ["mousedown", "mouseup", "click"].forEach((e) =>
        el.dispatchEvent(new MouseEvent(e, { bubbles: true, cancelable: true, view: window })));
      return { clicked: true, row_id: el.id };
    }, symbol);
  }

  out.bind_attempts = [];
  for (let attempt = 0; attempt < 3; attempt++) {
    await typeSymbol();
    let info = await clickSuggestion();
    await page.waitForTimeout(2200);
    out.bound = await page.evaluate(() => {
      const all = Array.from(document.querySelectorAll("#fulltextContainer"));
      const el = all.find((x) => {
        const r = x.getBoundingClientRect();
        return r.width > 0 && r.height > 0;
      }) || all[0];
      return (el?.innerText || "").trim().slice(0, 40);
    });
    if (!out.bound || !norm(out.bound).includes(norm(symbol))) {
      info = { ...info, ...(await gridFallback()) };
      await page.waitForTimeout(2000);
      out.bound = await page.evaluate(() => {
        const all = Array.from(document.querySelectorAll("#fulltextContainer"));
        const el = all.find((x) => {
          const r = x.getBoundingClientRect();
          return r.width > 0 && r.height > 0;
        }) || all[0];
        return (el?.innerText || "").trim().slice(0, 40);
      });
    }
    out.bind_attempts.push(info);
    if (out.bound && norm(out.bound).includes(norm(symbol))) break;
    await page.waitForTimeout(800);
  }
  if (!out.bound || !norm(out.bound).includes(norm(symbol))) {
    out.message = "bind_mismatch wanted=" + symbol + " got=" + (out.bound || "");
    console.log(JSON.stringify(out));
    await ctx.close();
    return;
  }

  // ---- quantity / price, typed and read back -----------------------------
  const bodyText = await page.evaluate(() => (document.body.innerText || "").replace(/\s+/g, " "));
  const lm = bodyText.match(/آستانه\s*حجم[^\d]{0,6}([\d,]+)\s*[-–]\s*([\d,]+)/);
  out.limit = lm ? parseInt(lm[2].replace(/,/g, ""), 10) : 0;
  const panelPrice = await page.evaluate(() =>
    parseFloat(document.querySelector("#send_order_txtPrice")?.value || "0") || 0);
  out.qty = out.limit ? Math.min(wantQty || out.limit, out.limit) : (wantQty || 0);
  const intended = wantPrice || panelPrice;
  out.intended_price = intended || null;

  for (let attempt = 0; attempt < 3; attempt++) {
    await setVal("#send_order_txtCount", out.qty);
    await setVal("#send_order_txtPrice", intended);
    await page.waitForTimeout(250);
    const c = (await readVal("#send_order_txtCount")).replace(/[,\s]/g, "");
    const p = (await readVal("#send_order_txtPrice")).replace(/[,\s]/g, "");
    out.count_ok = c === String(out.qty);
    out.price_ok = String(p) === String(intended);
    if (out.count_ok && out.price_ok) break;
  }
  if (!out.count_ok || !out.price_ok) {
    out.message = `field_mismatch intended=${intended} price_ok=${out.price_ok} count_ok=${out.count_ok}`;
    console.log(JSON.stringify(out));
    await ctx.close();
    return;
  }

  // ---- click the ticket button ------------------------------------------
  const buttonName = side === "sell" ? "فروش" : "خرید";
  // ---- precise send time (Hark fix #1) ----------------------------------
  // FLOW_AT_EPOCH_MS = the OS-clock epoch (ms) at which the click must happen,
  // e.g. 08:45:01.000 Tehran. Everything above (session, bind, fields) is done
  // before it, so the order leaves at the exact second instead of minutes later.
  const atMs = parseInt(process.env.FLOW_AT_EPOCH_MS || "0", 10);
  if (atMs > 0) {
    const lead = atMs - Date.now();
    out.waited_ms = Math.max(0, lead);
    if (lead > 15 * 60 * 1000) {
      out.message = "send_time_too_far";
      console.log(JSON.stringify(out));
      await ctx.close();
      return;
    }
    while (Date.now() < atMs - 1500) await page.waitForTimeout(Math.min(250, atMs - 1500 - Date.now()));
    // the panel may have re-rendered while we waited: re-assert the fields, then
    // spend the last 1.5 s idle so the click itself is not delayed
    await setVal("#send_order_txtCount", out.qty);
    await setVal("#send_order_txtPrice", intended);
    while (Date.now() < atMs - 30) await page.waitForTimeout(Math.min(250, Math.max(1, atMs - Date.now() - 20)));
    while (Date.now() < atMs) { /* spin the last few ms */ }
  }
  if (process.env.FLOW_DRY === "1") {
    out.click_epoch_ms = Date.now();          // rehearsal: when the click WOULD happen
    out.message = "dry-run: form verified, order NOT submitted";
    console.log(JSON.stringify(out));
    await ctx.close();
    return;
  }
  out.click_epoch_ms = Date.now();
  try {
    await page.getByRole("link", { name: buttonName }).first().click({ timeout: 8000 });
  } catch (e) {
    await page.evaluate(() => {
      const a = document.getElementById("send_order_btnBuySell");
      if (a) { a.scrollIntoView({ block: "center" }); a.click(); }
    }).catch(() => {});
  }
  await page.waitForTimeout(6000);

  // agreement modal
  const accepted = await page.evaluate(() => {
    let btn = null;
    document.querySelectorAll("button,a,span,div").forEach((x) => {
      if (btn) return;
      const t = (x.innerText || "").trim();
      if (/^(تایید|تأیید|موافقم|می‌پذیرم|قبول|پذیرش)$/.test(t) && x.offsetParent !== null) btn = x;
    });
    if (!btn) return false;
    ["mousedown", "mouseup", "click"].forEach((e) =>
      btn.dispatchEvent(new MouseEvent(e, { bubbles: true, cancelable: true, view: window })));
    return true;
  });
  if (accepted) {
    await page.waitForTimeout(3000);
    try { await page.getByRole("link", { name: buttonName }).first().click({ timeout: 8000 }); }
    catch (e) {}
    await page.waitForTimeout(6000);
  }

  // ---- did the broker accept it? (from the captured Post response) --------
  for (const c of out.calls) {
    if (c.req !== "Order/Post") continue;
    try {
      const d = JSON.parse(c.body);
      if (d.IsSuccessfull && d.Data && Number(d.Data.OrderId) > 0) {
        out.submitted = true;
        out.placed_orderid = String(d.Data.OrderId);
        out.message = `سفارش با موفقیت ثبت شد (OrderId=${out.placed_orderid})`;
      } else if (d.IsSuccessfull === false && d.MessageDesc) {
        out.message = String(d.MessageDesc);
      }
    } catch (e) {}
  }
  if (!out.submitted) {
    out.message = out.message || "no_order_response";
  }

  // ---- verify the price the broker recorded ------------------------------
  try {
    const raw = await page.evaluate(async (api) => {
      const r = await fetch(api + "/Order/GetOpenOrder/OpenOrder", { credentials: "include" });
      return await r.text();
    }, API);
    const d = JSON.parse(raw);
    // same symbol AND same side, so an older buy never "proves" a new sell (and vice versa)
    const sideFa = side === "sell" ? "فروش" : "خرید";
    const mine = (d.Data || []).find((o) => String(o.symbol || "").trim() === symbol &&
                                            String(o.orderside || "").includes(sideFa));
    if (mine) {
      out.placed_price = parseFloat(mine.orderprice);
      out.placed_orderid = String(mine.orderid || out.placed_orderid || "");
      if (!out.submitted && /no_order_response/.test(out.message || "")) {
        out.message = `ثبت شد (تأیید از سفارشات باز، OrderId=${out.placed_orderid})`;
      }
      out.submitted = true;
      if (out.intended_price && Math.abs(out.placed_price - out.intended_price) > 0.5) {
        out.mismatch = { intended: out.intended_price, placed: out.placed_price };
      }
    }
  } catch (e) {}

  console.log(JSON.stringify(out));
  await ctx.close();
})();
