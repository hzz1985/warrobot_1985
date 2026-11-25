// Shared broker-session helpers.
// Root cause of the constant logouts: every script relaunched Chrome, and
// (a) the "login needed" test used offsetParent (true for the hidden form),
// (b) session cookies are not restored by Playwright persistent contexts,
// so each run did a real login and the broker kicked the operator's session.
const fs = require("fs");
const path = require("path");

const BASE = path.join(__dirname, "..");
const COOKIES = path.join(BASE, "state", "pw_cookies.json");
const EVENTS = path.join(BASE, "state", "session_events.jsonl");

function logEvent(kind, extra) {
  try {
    fs.appendFileSync(EVENTS, JSON.stringify({ at: new Date().toISOString(), kind, ...extra }) + "\n");
  } catch (e) {}
}

async function load(ctx) {
  try {
    const raw = fs.readFileSync(COOKIES, "utf8");
    const cookies = JSON.parse(raw);
    if (Array.isArray(cookies) && cookies.length) {
      // session cookies without expiry die with the browser; give them one
      const now = Math.floor(Date.now() / 1000);
      for (const c of cookies) if (!c.expires || c.expires < 0) c.expires = now + 30 * 86400;
      await ctx.addCookies(cookies);
      logEvent("cookies_loaded", { n: cookies.length });
    }
  } catch (e) {}
}

async function save(ctx) {
  try {
    const cookies = await ctx.cookies();
    fs.writeFileSync(COOKIES, JSON.stringify(cookies, null, 0), "utf8");
    logEvent("cookies_saved", { n: cookies.length });
  } catch (e) {}
}

const BUDGET = path.join(BASE, "state", "login_budget.json");

function _budget() {
  try { return JSON.parse(fs.readFileSync(BUDGET, "utf8")); } catch (e) { return {}; }
}

// A login kicks the operator out (single-session broker). The old rule ("at most
// 2 per day and only 07:00-10:59") made every exit after 11:00 and every run after
// the 2nd morning login fail with login_blocked (exits_log 1405-09-02/03).
// With the shared browser (cdp_shared.js) a login is only needed when the ONE
// session really died, so the rule is now a throttle, not a ban:
//   * at least LOGIN_MIN_GAP_SEC (90 s) between two logins (no login storms)
//   * at most LOGIN_MAX_PER_DAY (30) per Tehran day
//   * state/OPERATOR_HOLD present -> no automatic login (operator works manually)
// SESSION_FORCE_LOGIN=1 still bypasses everything.
const HOLD = path.join(BASE, "state", "OPERATOR_HOLD");
function tehranDay() {
  const teh = new Date(Date.now() + 3.5 * 3600 * 1000);   // Tehran (UTC+3:30)
  return { day: teh.toISOString().slice(0, 10), hour: teh.getUTCHours() };
}
function loginAllowed() {
  if (process.env.SESSION_FORCE_LOGIN === "1") return { ok: true, reason: "forced" };
  const { day, hour } = tehranDay();
  if (fs.existsSync(HOLD)) return { ok: false, reason: "operator_hold", day };
  const b = _budget();
  const used = b.day === day ? (b.count || 0) : 0;
  const maxPerDay = parseInt(process.env.LOGIN_MAX_PER_DAY || "30", 10);
  const minGap = parseFloat(process.env.LOGIN_MIN_GAP_SEC || "90");
  if (used >= maxPerDay) return { ok: false, reason: "daily_login_budget_used", used, day };
  const last = b.at ? Date.parse(b.at) : 0;
  if (last && (Date.now() - last) / 1000 < minGap)
    return { ok: false, reason: "login_throttled", used, day, hour };
  return { ok: true, reason: "allowed", used, day, hour };
}

function noteLogin() {
  const d = new Date();
  const teh = new Date(d.getTime() + (3.5 * 3600 * 1000));
  const day = teh.toISOString().slice(0, 10);
  const b = _budget();
  const count = b.day === day ? (b.count || 0) + 1 : 1;
  try { fs.writeFileSync(BUDGET, JSON.stringify({ day, count, at: new Date().toISOString() })); } catch (e) {}
  logEvent("login", { day, count });
}

async function needLogin(page) {
  return await page.evaluate(() => {
    if (!/login/i.test(location.href)) return false;
    const u = document.querySelector("#username");
    if (!u) return false;
    const r = u.getBoundingClientRect();
    const st = window.getComputedStyle(u);
    return r.width > 0 && r.height > 0 && st.visibility !== "hidden" && st.display !== "none";
  });
}

function isLoginPage(url) { return /login/i.test(String(url || "")); }

// clear-then-set with the native setter (AutoFill must be overwritten), then submit
async function fillLogin(page, user, password, code) {
  await page.evaluate(({ u, p, c }) => {
    const set = (sel, val) => {
      const el = document.querySelector(sel);
      if (!el) return;
      const proto = el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype
                                                      : HTMLInputElement.prototype;
      const setter = Object.getOwnPropertyDescriptor(proto, "value").set;
      setter.call(el, "");
      setter.call(el, String(val));
      el.dispatchEvent(new Event("input", { bubbles: true }));
      el.dispatchEvent(new Event("change", { bubbles: true }));
    };
    set("#username", u); set("#password", p); set("#captcha", c);
    if (typeof submitForm === "function") submitForm();
    else document.getElementById("main-form")?.click();
  }, { u: user, p: password, c: code });
}

module.exports = { load, save, needLogin, loginAllowed, noteLogin, logEvent, COOKIES,
                   isLoginPage, fillLogin };
