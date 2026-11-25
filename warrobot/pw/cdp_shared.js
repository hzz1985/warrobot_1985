// Shared broker browser (Hark fix, bug #2).
//
// ROOT CAUSE this removes: every node script called
// chromium.launchPersistentContext(state/pw-profile) -> a brand-new Chrome, a
// brand-new login (captcha) and therefore a new broker session. The broker is
// single-session, so each new login kicked the previous one (and the operator),
// and pw_lock/pw_reset killed Chrome before/after every run. Dozens of logins per
// market hour = "can't log in" / "kicked out of the broker page".
//
// What this module does (required as the first line of every pw/*.js that uses
// the bot profile):
//   * patches chromium.launchPersistentContext(<...pw-profile>) so it ATTACHES to
//     ONE long-lived Chrome (started with --remote-debugging-port, default 9333,
//     on the same pw-profile) instead of launching a new one;
//   * hands the script the already logged-in broker tab and turns
//     page.goto("https://online.meibours.ir/...") into a no-op while that tab is
//     authenticated (navigation after login kills the session - BROKER_KNOWLEDGE §1);
//   * ctx.close() only closes tabs the script opened and disconnects - the shared
//     Chrome and its session stay alive;
//   * ctx.addCookies() is ignored (restoring an OLD cookie jar over a live session
//     logs it out);
//   * serialises scripts on the single tab with a cross-process lock
//     (state/ui.lock) and gives the morning order flow priority
//     (state/ui_priority, written by morning_orders.py).
//
// Rollback switch: set WARROBOT_SHARED_BROWSER=0 -> original behaviour.
"use strict";
const fs = require("fs");
const path = require("path");
const { spawn } = require("child_process");

const BASE = path.join(__dirname, "..");
const STATE = path.join(BASE, "state");
const PROFILE = path.join(STATE, "pw-profile");
const UI_LOCK = path.join(STATE, "ui.lock");
const UI_PRIORITY = path.join(STATE, "ui_priority");
const EVENTS = path.join(STATE, "session_events.jsonl");
const BROKER_HOST = "online.meibours.ir";

function cdpEndpoint() {
  if (process.env.WARROBOT_CDP) return process.env.WARROBOT_CDP;
  try {
    const cfg = JSON.parse(fs.readFileSync(path.join(BASE, "config.json"), "utf8"));
    const a = (cfg.broker || {}).debugger_address;
    if (a) return a.startsWith("http") ? a : "http://" + a;
  } catch (e) {}
  return "http://127.0.0.1:9333";
}

function logEvent(kind, extra) {
  try {
    fs.appendFileSync(EVENTS, JSON.stringify({ at: new Date().toISOString(), kind,
      script: path.basename(process.argv[1] || ""), pid: process.pid, ...(extra || {}) }) + "\n");
  } catch (e) {}
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function pidAlive(pid) {
  if (!pid || pid <= 0) return false;
  try { process.kill(pid, 0); return true; } catch (e) { return e.code === "EPERM"; }
}

function readLock(p) {
  try {
    const [pid, ts, name] = fs.readFileSync(p, "utf8").split("|");
    return { pid: parseInt(pid, 10) || 0, ts: parseFloat(ts) || 0, name: name || "" };
  } catch (e) { return null; }
}

// ---- cross-process lock on the single broker tab ---------------------------
const LOCK_STALE_SEC = parseFloat(process.env.WARROBOT_UI_LOCK_STALE || "600");
async function acquireUiLock(waitSec) {
  const prio = process.env.WARROBOT_UI_PRIORITY === "1";
  const t0 = Date.now();
  while ((Date.now() - t0) / 1000 < waitSec) {
    // non-priority scripts step aside while the morning order flow owns the tab
    if (!prio) {
      const pr = readLock(UI_PRIORITY);
      if (pr && pidAlive(pr.pid) && (Date.now() / 1000 - pr.ts) < 1800) { await sleep(500); continue; }
    }
    try {
      const fd = fs.openSync(UI_LOCK, "wx");
      fs.writeSync(fd, `${process.pid}|${Date.now() / 1000}|${path.basename(process.argv[1] || "")}`);
      fs.closeSync(fd);
      return true;
    } catch (e) {
      const cur = readLock(UI_LOCK);
      if (cur && cur.pid === process.pid) return true;
      const age = cur ? Date.now() / 1000 - cur.ts : 1e9;
      if (!cur || !pidAlive(cur.pid) || age > LOCK_STALE_SEC) {
        try { fs.unlinkSync(UI_LOCK); } catch (e2) {}
        logEvent("ui_lock_stale_removed", { owner: cur });
        continue;
      }
    }
    await sleep(300);
  }
  return false;
}
function releaseUiLock() {
  const cur = readLock(UI_LOCK);
  if (cur && cur.pid === process.pid) { try { fs.unlinkSync(UI_LOCK); } catch (e) {} }
}
process.on("exit", releaseUiLock);

// ---- shared Chrome ----------------------------------------------------------
function chromePath() {
  const cands = [
    process.env.WARROBOT_CHROME,
    "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
    "C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe",
    path.join(process.env.LOCALAPPDATA || "", "Google", "Chrome", "Application", "chrome.exe"),
  ].filter(Boolean);
  return cands.find((p) => { try { return fs.existsSync(p); } catch (e) { return false; } });
}

async function cdpAlive(ep) {
  try {
    const r = await fetch(ep.replace(/\/$/, "") + "/json/version", { signal: AbortSignal.timeout(2500) });
    return r.ok;
  } catch (e) { return false; }
}

function chromeArgs(ep) {
  const port = (ep.match(/:(\d+)/) || [])[1] || "9333";
  return [
    `--remote-debugging-port=${port}`, "--remote-debugging-address=127.0.0.1",
    "--remote-allow-origins=*",
    `--user-data-dir=${PROFILE}`, "--no-first-run", "--no-default-browser-check",
    "--disable-background-timer-throttling", "--disable-renderer-backgrounding",
    "--disable-backgrounding-occluded-windows",
    "https://online.meibours.ir/",
  ];
}

async function ensureChrome(ep) {
  if (await cdpAlive(ep)) return true;
  const exe = chromePath();
  if (!exe) return false;
  logEvent("shared_chrome_start", { ep });
  try {
    const ch = spawn(exe, chromeArgs(ep), { detached: true, stdio: "ignore", windowsHide: false });
    ch.unref();
  } catch (e) { return false; }
  for (let i = 0; i < 40; i++) { await sleep(500); if (await cdpAlive(ep)) return true; }
  return false;
}

function isBrokerUrl(u) { return String(u || "").includes(BROKER_HOST); }
function isLoginUrl(u) { return /login/i.test(String(u || "")); }

function pickMainPage(ctx) {
  const pages = ctx.pages();
  return pages.find((p) => isBrokerUrl(p.url()) && !isLoginUrl(p.url()))
      || pages.find((p) => isBrokerUrl(p.url()))
      || pages[0] || null;
}

function wrapPage(page) {
  if (!page || page.__warrobotWrapped) return page;
  const origGoto = page.goto.bind(page);
  page.goto = async (url, opts) => {
    const cur = page.url();
    // never navigate an authenticated broker tab (that drops the session)
    if (isBrokerUrl(url) && isBrokerUrl(cur) && !isLoginUrl(cur)) {
      logEvent("goto_skipped", { url, cur });
      return null;
    }
    return origGoto(url, opts);
  };
  const origClose = page.close.bind(page);
  page.close = async () => { logEvent("main_page_close_ignored"); };
  page.__warrobotOrigClose = origClose;
  page.__warrobotWrapped = true;
  return page;
}

function install() {
  if (process.env.WARROBOT_SHARED_BROWSER === "0") return;
  let pw;
  try { pw = require("playwright"); } catch (e) { return; }
  const chromium = pw.chromium;
  if (!chromium || chromium.__warrobotShared) return;
  const origLaunch = chromium.launchPersistentContext.bind(chromium);

  chromium.launchPersistentContext = async (userDataDir, options) => {
    const isBot = path.resolve(String(userDataDir || "")).toLowerCase() === path.resolve(PROFILE).toLowerCase();
    if (!isBot) return origLaunch(userDataDir, options);
    const ep = cdpEndpoint();
    const waitSec = parseFloat(process.env.WARROBOT_UI_LOCK_WAIT || "300");
    const gotLock = await acquireUiLock(waitSec);
    if (!gotLock) logEvent("ui_lock_timeout", { waitSec });
    let browser = null;
    try {
      if (await ensureChrome(ep)) browser = await chromium.connectOverCDP(ep, { timeout: 20000 });
    } catch (e) { browser = null; logEvent("cdp_connect_failed", { err: String(e).slice(0, 160) }); }
    if (!browser) {
      // degraded mode: the old behaviour (separate Chrome) so nothing is worse than before
      logEvent("fallback_launch_persistent");
      const ctx0 = await origLaunch(userDataDir, options);
      const c0 = ctx0.close.bind(ctx0);
      ctx0.close = async () => { try { await c0(); } finally { releaseUiLock(); } };
      return ctx0;
    }
    const real = browser.contexts()[0] || await browser.newContext();
    let main = pickMainPage(real);
    if (!main) main = await real.newPage();
    wrapPage(main);
    const opened = [];
    let closed = false;
    const ctx = new Proxy(real, {
      get(target, prop) {
        if (prop === "pages") return () => [main, ...target.pages().filter((p) => p !== main)];
        if (prop === "newPage") return async () => { const p = await target.newPage(); opened.push(p); return p; };
        if (prop === "addCookies") return async () => { logEvent("addCookies_ignored_shared"); };
        if (prop === "close") return async () => {
          if (closed) return; closed = true;
          for (const p of opened) { try { await p.close(); } catch (e) {} }
          try { await browser.close(); } catch (e) {}   // CDP: disconnect only
          releaseUiLock();
        };
        if (prop === "__warrobotShared") return true;
        const v = target[prop];
        return typeof v === "function" ? v.bind(target) : v;
      },
    });
    logEvent("attached_shared", { url: main.url() });
    return ctx;
  };
  chromium.__warrobotShared = true;
}

install();
module.exports = { cdpEndpoint, ensureChrome, cdpAlive, acquireUiLock, releaseUiLock, chromeArgs, isLoginUrl };
