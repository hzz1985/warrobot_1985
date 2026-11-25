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

// A login kicks the operator out (single-session broker). So: at most 2 per day,
// and only in the pre-market window, unless SESSION_FORCE_LOGIN=1 is set.
function loginAllowed() {
  if (process.env.SESSION_FORCE_LOGIN === "1") return { ok: true, reason: "forced" };
  const d = new Date();
  const teh = new Date(d.getTime() + (3.5 * 3600 * 1000));      // Tehran (UTC+3:30)
  const day = teh.toISOString().slice(0, 10);
  const hour = teh.getUTCHours();
  const b = _budget();
  const used = b.day === day ? (b.count || 0) : 0;
  if (used >= 2) return { ok: false, reason: "daily_login_budget_used", used, day };
  if (hour < 7 || hour > 10) return { ok: false, reason: "outside_premarket_window", hour, day };
  return { ok: true, reason: "allowed", used, day };
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

module.exports = { load, save, needLogin, loginAllowed, noteLogin, logEvent, COOKIES };
