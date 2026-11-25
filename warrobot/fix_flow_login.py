"""flow.js login: same robust fix as balance.js (native setter + visibility check).

The order flow crashed on `page.click("#captcha")` (Timeout 30000ms) which is why
no order could be submitted on 1405/08/27. Fields are now set through the native
value setter with input/change events, and the login is skipped when the panel is
already authenticated.
"""
from pathlib import Path

p = Path(r"C:\Users\Administrator\Desktop\DeepSeek DeskTop\WarRobot\pw\flow.js")
s = p.read_text(encoding="utf-8")

if "async function setValFlow" not in s:
    s = s.replace('''  // login
  await page.goto("https://online.meibours.ir/", { waitUntil: "domcontentloaded", timeout: 60000 });''',
'''  async function setValFlow(sel, val) {
    return await page.evaluate(({ sel, val }) => {
      const el = document.querySelector(sel);
      if (!el) return false;
      const proto = el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype
                                                      : HTMLInputElement.prototype;
      const setter = Object.getOwnPropertyDescriptor(proto, "value").set;
      setter.call(el, String(val));
      el.dispatchEvent(new Event("input", { bubbles: true }));
      el.dispatchEvent(new Event("change", { bubbles: true }));
      return true;
    }, { sel, val });
  }
  async function loginFormVisible() {
    return await page.evaluate(() => {
      const u = document.querySelector("#username");
      return !!(u && u.offsetParent !== null);
    });
  }

  // login
  await page.goto("https://online.meibours.ir/", { waitUntil: "domcontentloaded", timeout: 60000 });''')

    s = s.replace('''  if (page.url().includes("Login")) {
    let code = "";
    for (let i = 0; i < 3; i++) {
      await page.click("#captcha-img").catch(() => {});
      await page.waitForTimeout(2500);
      code = solve(await page.getAttribute("#captcha-img", "src").catch(() => ""));
      if (code.length === 4) break;
    }
    for (const [sel, val] of [["#username", CREDS.user], ["#password", CREDS.password], ["#captcha", code]]) {
      await page.click(sel); await page.keyboard.press("Control+A"); await page.keyboard.press("Backspace");
      await page.keyboard.type(val, { delay: 20 });
    }
    await page.evaluate(() => { if (typeof submitForm === "function") submitForm(); else document.getElementById("main-form")?.click(); });
    await page.waitForTimeout(12000);
  }''',
'''  if (await loginFormVisible()) {
    let code = "";
    for (let i = 0; i < 4; i++) {
      await page.click("#captcha-img", { force: true }).catch(() => {});
      await page.waitForTimeout(2000);
      code = solve(await page.getAttribute("#captcha-img", "src").catch(() => ""));
      if (code.length === 4) break;
    }
    await setValFlow("#username", CREDS.user);
    await setValFlow("#password", CREDS.password);
    await setValFlow("#captcha", code);
    await page.evaluate(() => { if (typeof submitForm === "function") submitForm(); else document.getElementById("main-form")?.click(); });
    await page.waitForTimeout(12000);
  }''')
    p.write_text(s, encoding="utf-8")
    print("flow.js login hardened")
else:
    print("already patched")
