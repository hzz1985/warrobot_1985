// Simulation of pw/cdp_shared.js with a fake "playwright" and a fake CDP endpoint.
//   node tests/js/test_cdp_shared.js   (exit code 0 = pass)
"use strict";
const fs = require("fs"), os = require("os"), path = require("path"), http = require("http");
const assert = require("assert");
const { spawn } = require("child_process");

const SRC = path.join(__dirname, "..", "..", "pw", "cdp_shared.js");
const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "cdpshared-"));
fs.mkdirSync(path.join(tmp, "pw", "node_modules", "playwright"), { recursive: true });
fs.mkdirSync(path.join(tmp, "state"), { recursive: true });
fs.copyFileSync(SRC, path.join(tmp, "pw", "cdp_shared.js"));
fs.writeFileSync(path.join(tmp, "pw", "node_modules", "playwright", "index.js"), `
function mkPage(url){ return { _url:url, gotos:0, closed:false, url(){return this._url},
  async goto(u){ this.gotos++; this._url=u; return {}; }, async close(){ this.closed=true; } }; }
const main = mkPage("https://online.meibours.ir/Home/Default/page-1");
const ctx = { _pages:[main], added:false, pages(){ return this._pages; },
  async newPage(){ const p = mkPage("about:blank"); this._pages.push(p); return p; },
  async addCookies(){ this.added = true; },
  async close(){ throw new Error("the default context cannot be closed"); } };
const browser = { disconnected:false, contexts(){ return [ctx]; }, async close(){ this.disconnected = true; } };
const state = { main, ctx, browser, launched:0 };
module.exports = { __state: state, chromium: {
  async launchPersistentContext(){ state.launched++; return { async close(){}, pages(){ return []; } }; },
  async connectOverCDP(){ return browser; } } };
`);

(async () => {
  const server = http.createServer((req, res) => { res.writeHead(200); res.end("{}"); });
  await new Promise((r) => server.listen(0, "127.0.0.1", r));
  const port = server.address().port;
  fs.writeFileSync(path.join(tmp, "config.json"), JSON.stringify({ broker: { debugger_address: `127.0.0.1:${port}` } }));
  process.env.WARROBOT_CHROME = path.join(tmp, "no-chrome.exe");

  require(path.join(tmp, "pw", "cdp_shared.js"));
  const pw = require(path.join(tmp, "pw", "node_modules", "playwright"));
  const S = pw.__state;
  const PROFILE = path.join(tmp, "state", "pw-profile");

  // 1) attach instead of launch; the logged-in tab is reused, never navigated
  const ctx = await pw.chromium.launchPersistentContext(PROFILE, { headless: true });
  assert.strictEqual(S.launched, 0, "must not launch a second Chrome");
  assert.ok(fs.existsSync(path.join(tmp, "state", "ui.lock")), "ui lock held");
  const page = ctx.pages()[0];
  assert.strictEqual(page, S.main);
  await page.goto("https://online.meibours.ir/");
  assert.strictEqual(S.main.gotos, 0, "goto on an authenticated broker tab is skipped");
  await ctx.addCookies([{ name: "old" }]);
  assert.strictEqual(S.ctx.added, false, "old cookie jar never overwrites the live session");
  const extra = await ctx.newPage();
  await page.close();
  assert.strictEqual(S.main.closed, false, "main tab is never closed");
  await ctx.close();
  assert.strictEqual(extra.closed, true, "tabs the script opened are closed");
  assert.strictEqual(S.browser.disconnected, true, "only disconnects");
  assert.ok(!fs.existsSync(path.join(tmp, "state", "ui.lock")), "ui lock released");

  // 2) a logged-out tab IS navigated (so the login form appears)
  S.main._url = "https://online.meibours.ir/Account/Login";
  const ctx2 = await pw.chromium.launchPersistentContext(PROFILE, {});
  await ctx2.pages()[0].goto("https://online.meibours.ir/");
  assert.strictEqual(S.main.gotos, 1);
  await ctx2.close();

  // 3) the morning priority makes other scripts wait
  const holder = spawn(process.execPath, ["-e", "setTimeout(()=>{}, 20000)"]);
  fs.writeFileSync(path.join(tmp, "state", "ui_priority"), `${holder.pid}|${Date.now() / 1000}|morning`);
  process.env.WARROBOT_UI_LOCK_WAIT = "1.5";
  const t0 = Date.now();
  const ctx3 = await pw.chromium.launchPersistentContext(PROFILE, {});
  assert.ok(Date.now() - t0 >= 1400, "non-priority script waited for the morning flow");
  await ctx3.close();
  process.env.WARROBOT_UI_PRIORITY = "1";
  const t1 = Date.now();
  const ctx4 = await pw.chromium.launchPersistentContext(PROFILE, {});
  assert.ok(Date.now() - t1 < 1000, "priority script goes first");
  await ctx4.close();
  holder.kill();

  // 4) no CDP and no chrome.exe -> the old launchPersistentContext (degraded, not broken)
  server.close();
  await new Promise((r) => setTimeout(r, 200));
  const ctx5 = await pw.chromium.launchPersistentContext(PROFILE, {});
  assert.strictEqual(S.launched, 1, "fallback launch");
  await ctx5.close();

  console.log("cdp_shared: all assertions passed");
  process.exit(0);
})().catch((e) => { console.error(e); process.exit(1); });
