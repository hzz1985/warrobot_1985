// Keep the ONE shared broker session alive and re-login only when it is really gone.
//   node keepalive.js            -> check + keep-alive (+ login when logged out)
//   node keepalive.js --no-login -> check only
// Prints one JSON line: {ok, state, url, logged_in_now, api_status, reason}
require("./cdp_shared");
const fs = require("fs"), path = require("path"), { execFileSync } = require("child_process");
const { chromium } = require("playwright");
const SESSION = require("./session");

const BASE = path.join(__dirname, "..");
const PROFILE = path.join(BASE, "state", "pw-profile");
const OCR = path.join(BASE, "ocr", "read2.js");
const PNG = path.join(BASE, "data", "captcha_pw.png");
const API = "https://api.meibours.ir/Web/V1";

function creds() {
  return JSON.parse(fs.readFileSync(path.join(BASE, "state", "credentials.json"), "utf8"));
}
function solve(uri) {
  if (!uri || !uri.startsWith("data:image")) return "";
  fs.writeFileSync(PNG, Buffer.from(uri.split(",")[1], "base64"));
  try {
    const out = execFileSync(process.execPath, [OCR, PNG], { encoding: "utf8", timeout: 180000 });
    return String(JSON.parse((out || "{}").trim() || "{}").text || "");
  } catch (e) { return ""; }
}

(async () => {
  const noLogin = process.argv.includes("--no-login");
  const out = { ok: false, state: "unknown", url: "", logged_in_now: false, api_status: null };
  const ctx = await chromium.launchPersistentContext(PROFILE, { channel: "chrome", headless: false });
  const page = ctx.pages()[0] || await ctx.newPage();
  try {
    if (!/meibours/.test(page.url())) {
      await page.goto("https://online.meibours.ir/", { waitUntil: "domcontentloaded", timeout: 60000 });
      await page.waitForTimeout(3000);
    }
    // in-page API call = keep-alive AND the real "am I authenticated" test
    const probe = async () => page.evaluate(async (api) => {
      try {
        const r = await fetch(api + "/Accounting/Remain", { credentials: "include" });
        const t = await r.text();
        let ok = false;
        try { const d = JSON.parse(t); ok = !!(d && d.IsSuccessfull && d.Data); } catch (e) {}
        return { status: r.status, ok };
      } catch (e) { return { status: 0, ok: false, err: String(e).slice(0, 80) }; }
    }, API).catch(() => ({ status: 0, ok: false }));

    const onLogin = SESSION.isLoginPage(page.url());
    let p = onLogin ? { status: 0, ok: false } : await probe();
    if (!onLogin && !p.ok) { await page.waitForTimeout(3000); p = await probe(); }   // confirm
    out.api_status = p.status;
    // a network error (status 0) on an app page is NOT a logout: never reload then
    const loggedOut = onLogin || (await SESSION.needLogin(page)) ||
                      p.status === 401 || p.status === 403 || (p.status === 200 && !p.ok);
    if (p.ok) {
      out.ok = true; out.state = "alive";
    } else if (!loggedOut) {
      out.state = "api_unreachable";
    } else {
      out.state = "logged_out";
      if (!noLogin) {
        const gate = SESSION.loginAllowed();
        if (!gate.ok) {
          out.reason = gate.reason;
        } else {
          if (!(await SESSION.needLogin(page))) {
            // dead session on an app page: reloading it is safe now (nothing to lose)
            await page.goto("https://online.meibours.ir/", { waitUntil: "domcontentloaded", timeout: 60000 }).catch(() => {});
            await page.waitForTimeout(3000);
          }
          if (await SESSION.needLogin(page)) {
            SESSION.noteLogin();
            const c = creds();
            for (let attempt = 0; attempt < 2 && !out.ok; attempt++) {
              let code = "";
              for (let i = 0; i < 4; i++) {
                await page.click("#captcha-img", { force: true }).catch(() => {});
                await page.waitForTimeout(1800);
                code = solve(await page.getAttribute("#captcha-img", "src").catch(() => ""));
                if (code.length === 4) break;
              }
              await SESSION.fillLogin(page, c.user, c.password, code);
              await page.waitForTimeout(10000);
              if (!SESSION.isLoginPage(page.url())) {
                p = await probe();
                out.ok = !!p.ok; out.api_status = p.status;
              }
            }
            out.logged_in_now = out.ok;
            out.state = out.ok ? "relogged" : "login_failed";
          }
        }
      }
    }
    out.url = page.url();
  } catch (e) {
    out.error = String(e).slice(0, 160);
  }
  SESSION.logEvent("keepalive", { ok: out.ok, state: out.state, api: out.api_status });
  console.log(JSON.stringify(out));
  await ctx.close();
})();
