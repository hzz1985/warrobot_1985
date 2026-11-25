"""Hardening sweep: every broker script must survive an un-clickable login form.

On 1405/08/27 the pre-open chain failed because several Playwright scripts still
typed the login form by clicking each input; when an input was not clickable the
click timed out and the whole script crashed (balance 0 -> no order).

This sweep rewrites the login block in every pw/*.js that still uses it:
  * skip the login when the panel is already authenticated
  * set the values via the native setter (works without a clickable element)
  * force the captcha image click and never let it abort the run
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
async function loginFormVisible() {
  return await page.evaluate(() => {
    const u = document.querySelector("#username");
    return !!(u && u.offsetParent !== null);
  });
}
'''

TYPING_LOOP = '''for (const [s,v] of [["#username",CREDS.user],["#password",CREDS.password],["#captcha",c]]) {'''

NEW_TYPING = '''await setVal("#username", CREDS.user);
   await setVal("#password", CREDS.password);
   await setVal("#captcha", c);'''


def patch(path: Path) -> str:
    s = path.read_text(encoding="utf-8")
    if "async function setVal" not in s:
        anchor = "async()=>{"
        i = s.find(anchor)
        if i < 0:
            return "skip (no anchor)"
        s = s[:i + len(anchor)] + HELPER + s[i + len(anchor):]
    changed = False
    # replace the click+type loop (several spacing variants exist)
    for pattern in (
        '''for (const [s,v] of [["#username",CREDS.user],["#password",CREDS.password],["#captcha",c]]) {
   await page.click(s); await page.keyboard.press("Control+A"); await page.keyboard.press("Backspace"); await page.keyboard.type(v,{delay:20}); }''',
        '''for (const [sel, val] of [["#username", CREDS.user], ["#password", CREDS.password], ["#captcha", code]]) {
      await page.click(sel); await page.keyboard.press("Control+A"); await page.keyboard.press("Backspace");
      await page.keyboard.type(val, { delay: 20 });
    }''',
    ):
        if pattern in s:
            s = s.replace(pattern, NEW_TYPING)
            changed = True
    # force the captcha image click and guard the login block by visibility
    s = s.replace('await page.click("#captcha-img").catch(()=>{});',
                  'await page.click("#captcha-img",{force:true}).catch(()=>{});')
    s = s.replace('await page.click("#captcha-img").catch(() => {});',
                  'await page.click("#captcha-img", { force: true }).catch(() => {});')
    s = s.replace('if (page.url().includes("Login")) {',
                  'if (await loginFormVisible()) {')
    if changed or "setVal(" in s:
        path.write_text(s, encoding="utf-8")
        return "patched"
    return "no change"


for f in sorted(PW.glob("*.js")):
    if "node_modules" in str(f):
        continue
    try:
        res = patch(f)
    except Exception as exc:
        res = f"error {str(exc)[:60]}"
    print(f"{f.name:32s} {res}")
