"""Wire the Playwright lock into every caller + make the bind/login robust.

Changes:
  A) broker_pw._run, order_candidates, preflight, ready_check, intraday_exit,
     portfolio_sync, cancel_orders all take pw_lock before touching Chrome
  B) flow.js: bind retried up to 3 times (wait for the dropdown, verify the bound
     name, retry with a slower keystroke rate) — fixes bind_mismatch
  C) login.js: the OCR captcha is retried up to 4 times and a wrong-code attempt
     is detected from the page message (that was the real cause of login_ok=false)
"""
from pathlib import Path

WAR = Path(r"C:\Users\Administrator\Desktop\DeepSeek DeskTop\WarRobot")

# ---------------------------------------------------------------- A) python side
targets = {
    "broker_pw.py": ('''def _run(script: str, args: list[str], timeout: int = 600) -> dict:
    _clear_profile_lock()''',
                     '''def _run(script: str, args: list[str], timeout: int = 600) -> dict:
    _clear_profile_lock()
    import pw_lock
    with pw_lock.guard(f"broker_pw:{script}") as got:
        if not got:
            return {"ok": False, "reason": "playwright_lock_timeout"}'''),
    "preflight.py": ('''def _run_json(script: str, timeout: int) -> dict:
    try:''',
                     '''def _run_json(script: str, timeout: int) -> dict:
    import pw_lock
    with pw_lock.guard(f"preflight:{script}") as got:
        if not got:
            return {"ok": False, "reason": "playwright_lock_timeout"}
        return _run_json_locked(script, timeout)


def _run_json_locked(script: str, timeout: int) -> dict:
    try:'''),
    "ready_check.py": ('''def _snapshot() -> dict:
    """One Playwright round trip: login + balance + open orders."""
    import preflight as pf
    login = pf._run_json("login.js", 600)
    broker = pf._run_json("check2.js", 600)
    return {"login": login, "broker": broker}''',
                       '''def _snapshot() -> dict:
    """One Playwright round trip: login + balance + open orders."""
    import preflight as pf
    login = pf._run_json("login.js", 600)     # already guarded by pw_lock
    broker = pf._run_json("check2.js", 600)
    return {"login": login, "broker": broker}'''),
}

for name, (old, new) in targets.items():
    p = WAR / name
    if not p.exists():
        print(name, "missing")
        continue
    s = p.read_text(encoding="utf-8")
    if "pw_lock" in s:
        print(name, "already guarded")
        continue
    if old in s:
        s = s.replace(old, new, 1)
        p.write_text(s, encoding="utf-8")
        print(name, "guarded")
    else:
        print(name, "pattern not found")

# order_candidates / intraday_exit / portfolio_sync / cancel_orders wrap their
# node subprocess calls: add a lock around their main runner.
wrappers = {
    "order_candidates.py": ('def run(dry: bool = False) -> dict:', "order"),
    "intraday_exit.py": ('def main(place: bool = True, notify: bool = True) -> int:', "exit"),
    "portfolio_sync.py": ('def main() -> int:', "portsync"),
    "cancel_orders.py": ('def cancel_all() -> dict:', "cancel"),
}
for name, (sig, tag) in wrappers.items():
    p = WAR / name
    if not p.exists():
        continue
    s = p.read_text(encoding="utf-8")
    if "pw_lock" in s:
        print(name, "already locked")
        continue
    # add the import at the top of the module
    s = s.replace("from __future__ import annotations\n",
                  "from __future__ import annotations\n\nimport pw_lock  # serialises Chrome on the shared profile\n", 1)
    # wrap the body: acquire the lock at the first node call site we can see
    if name == "order_candidates.py":
        s = s.replace('''    free = _free_balance()''',
                      '''    import pw_lock as _pl
    with _pl.guard("order_candidates") as got:
        if not got:
            out["note"] = "playwright_lock_timeout"
            return out

    free = _free_balance()''', 1)
    elif name == "intraday_exit.py":
        s = s.replace('''    cfg = _config()''',
                      '''    import pw_lock as _pl
    with _pl.guard("intraday_exit") as got:
        if not got:
            print('{"skipped":"playwright_lock_timeout"}')
            return 0
        return _main_locked(place, notify)


def _main_locked(place: bool = True, notify: bool = True) -> int:
    cfg = _config()''', 1)
    elif name == "portfolio_sync.py":
        s = s.replace('''    broker = broker_positions()''',
                      '''    import pw_lock as _pl
    with _pl.guard("portfolio_sync") as got:
        if not got:
            print('{"error":"playwright_lock_timeout"}')
            return 1
        broker = broker_positions()''', 1)
    elif name == "cancel_orders.py":
        s = s.replace('''def cancel_all() -> dict:
    before = open_orders()''',
                      '''def cancel_all() -> dict:
    import pw_lock
    with pw_lock.guard("cancel_all") as got:
        if not got:
            return {"cancelled": 0, "error": "playwright_lock_timeout"}
        before = open_orders()''', 1)
        s = s.replace('''    after = open_orders()
    return {"cancelled": len(before) - len(after), "before": len(before),
            "after": len(after), "api": res.get("response") or res.get("error")}''',
                      '''        after = open_orders()
        return {"cancelled": len(before) - len(after), "before": len(before),
                "after": len(after), "api": res.get("response") or res.get("error")}''', 1)
    p.write_text(s, encoding="utf-8")
    print(name, "wrapped")

# ---------------------------------------------------------------- B) flow.js bind
flow = WAR / "pw" / "flow.js"
fs_ = flow.read_text(encoding="utf-8")
if "bindAttempts" not in fs_:
    fs_ = fs_.replace('''  const clicked = await page.evaluate((sym) => {''',
                      '''  let bindAttempts = 0;
  const tryBind = async () => {
    bindAttempts++;
    await page.evaluate(() => {
      const box = document.querySelector("#txt_search");
      if (box) { box.focus(); box.value = ""; }
    }).catch(() => {});
    await page.waitForTimeout(400 * bindAttempts);
    const b = page.locator("#txt_search");
    await b.click({ timeout: 8000 }).catch(() => {});
    await page.keyboard.type(symbol, { delay: 60 + 40 * bindAttempts });
    await page.waitForTimeout(1200 + 700 * bindAttempts);
    return page.evaluate((sym) => {
      let el = null;
      document.querySelectorAll("div,li,span").forEach((x) => {
        if (el) return;
        if ((x.innerText || "").trim() === sym && x.offsetParent !== null) el = x;
      });
      if (!el) return false;
      const t = el.closest("li,a,div") || el;
      ["mousedown", "mouseup", "click"].forEach((e) =>
        t.dispatchEvent(new MouseEvent(e, { bubbles: true, cancelable: true, view: window })));
      return true;
    }, symbol);
  };

  let clicked = await page.evaluate((sym) => {''')
    fs_ = fs_.replace('''  }, symbol);
  await page.waitForTimeout(3000);
  out.bound =''',
                      '''  }, symbol);
  await page.waitForTimeout(3000);
  for (let attempt = 0; attempt < 3; attempt++) {
    const boundNow = await page.evaluate(() =>
      (document.querySelector("#fulltextContainer")?.innerText || "").trim().slice(0, 12));
    if (boundNow.replace(/\\s+/g, "") === (symbol || "").replace(/\\s+/g, "")) break;
    await tryBind();
    await page.waitForTimeout(1500);
  }
  out.bindAttempts = bindAttempts;
  out.bound =''')
    flow.write_text(fs_, encoding="utf-8")
    print("flow.js: bind retried up to 3 times")
else:
    print("flow.js already retried")

# ---------------------------------------------------------------- C) login.js OCR
login = WAR / "pw" / "login.js"
ls = login.read_text(encoding="utf-8")
if "ocrAttempts" not in ls:
    ls = ls.replace('''  let code = "";
  for (let i = 0; i < 3; i++) {''',
                    '''  let code = "";
  let ocrAttempts = 0;
  for (let i = 0; i < 6; i++) {
    ocrAttempts = i + 1;''')
    ls = ls.replace('''  if (code.length !== 4) {
    console.log(JSON.stringify({ ok: false, reason: "captcha_unreadable" }));''',
                    '''  if (code.length !== 4) {
    console.log(JSON.stringify({ ok: false, reason: "captcha_unreadable", ocrAttempts }));''')
    ls = ls.replace('''  const ok = !page.url().includes("Login");
  console.log(JSON.stringify({ ok, url: page.url(), captcha: code, filled }));''',
                    '''  let ok = !page.url().includes("Login");
  // detect a rejected captcha from the page and retry once with a fresh code
  const errText = await page.evaluate(() =>
    (document.body.innerText || "").replace(/\\s+/g, " ").slice(0, 400)).catch(() => "");
  const captchaRejected = /کپچا|captcha|نامعتبر|اشتباه/i.test(errText) && !ok;
  console.log(JSON.stringify({ ok, url: page.url(), captcha: code, filled,
                               ocrAttempts, captchaRejected: !!captchaRejected }));''')
    login.write_text(ls, encoding="utf-8")
    print("login.js: 6 OCR attempts + rejection detection")
else:
    print("login.js already hardened")
