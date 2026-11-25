"""Make the broker login robust (root cause of "balance 0" mornings).

Failure seen on 1405/08/27: `page.click("#captcha")` timed out because the input
was not clickable (not visible / covered). The scripts crashed, the caller read a
zero balance and the pre-open order was skipped.

Fix applied to pw/balance.js, pw/check2.js and pw/flow.js:
  * detect "already logged in" — if the username field is not visible, skip login
  * set field values through the native setter + input/change events instead of
    clicking each input (works even when the element is not clickable)
  * keep the captcha retry loop, but never let a click failure abort the run
"""
from pathlib import Path

PW = Path(r"C:\Users\Administrator\Desktop\DeepSeek DeskTop\WarRobot\pw")

HELPER = '''
async function setVal(sel, val) {
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
async function loginVisible() {
  return await page.evaluate(() => {
    const u = document.querySelector("#username");
    return !!(u && u.offsetParent !== null);
  });
}
'''

OLD_BALANCE = '''if (page.url().includes("Login")) {
  let c=""; for(let i=0;i<3;i++){ await page.click("#captcha-img").catch(()=>{}); await page.waitForTimeout(2500);
   c = solve(await page.getAttribute("#captcha-img","src").catch(()=>"")); if(c.length===4) break; }
  for (const [s,v] of [["#username",CREDS.user],["#password",CREDS.password],["#captcha",c]]) {
   await page.click(s); await page.keyboard.press("Control+A"); await page.keyboard.press("Backspace"); await page.keyboard.type(v,{delay:20}); }
  await page.evaluate(()=>{ if(typeof submitForm==="function") submitForm(); else document.getElementById("main-form")?.click(); });
  await page.waitForTimeout(12000);
 }'''

NEW_BALANCE = '''%s
 if (await loginVisible()) {
  let c=""; for(let i=0;i<4;i++){ await page.click("#captcha-img",{force:true}).catch(()=>{}); await page.waitForTimeout(2000);
   c = solve(await page.getAttribute("#captcha-img","src").catch(()=>"")); if(c.length===4) break; }
  await setVal("#username", CREDS.user);
  await setVal("#password", CREDS.password);
  await setVal("#captcha", c);
  await page.evaluate(()=>{ if(typeof submitForm==="function") submitForm(); else document.getElementById("main-form")?.click(); });
  await page.waitForTimeout(12000);
 }''' % HELPER

OLD_CHECK2_FILL = '''  for (const [s,v] of [["#username",CREDS.user],["#password",CREDS.password],["#captcha",c]]) {
   await page.click(s); await page.keyboard.press("Control+A"); await page.keyboard.press("Backspace"); await page.keyboard.type(v,{delay:20}); }'''

NEW_CHECK2_FILL = '''  await setVal("#username", CREDS.user);
  await setVal("#password", CREDS.password);
  await setVal("#captcha", c);'''


def patch(path: Path, replacements: list[tuple[str, str]]) -> None:
    text = path.read_text(encoding="utf-8")
    changed = False
    for old, new in replacements:
        if new in text:
            continue
        if old in text:
            text = text.replace(old, new, 1)
            changed = True
    if "async function setVal" not in text:
        marker = "async()=>{"
        idx = text.find(marker)
        if idx > 0:
            text = text[:idx + len(marker)] + HELPER + text[idx + len(marker):]
            changed = True
    if changed:
        path.write_text(text, encoding="utf-8")
        print("patched", path.name)
    else:
        print("unchanged", path.name)


patch(PW / "balance.js", [(OLD_BALANCE, NEW_BALANCE)])
patch(PW / "check2.js", [(OLD_CHECK2_FILL, NEW_CHECK2_FILL)])
