"""Second sweep: replace every remaining click+type login loop with setVal calls."""
from __future__ import annotations

import re
from pathlib import Path

PW = Path(r"C:\Users\Administrator\Desktop\DeepSeek DeskTop\WarRobot\pw")

HELPER = '''async function setVal(sel, val) {
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
'''

LOOP_RE = re.compile(
    r"for \(const \[(\w+), ?(\w+)\] of \[\[\"#username\", ?([^\]]+)\], ?\["
    r"\"#password\", ?([^\]]+)\], ?\[\"#captcha\", ?([^\]]+)\]\]"
    r"(?:, ?\[[^\]]*\])?\)\) \{[\s\S]*?\n?\s*\}",
)

patched = []
for f in sorted(PW.glob("*.js")):
    s = f.read_text(encoding="utf-8", errors="replace")
    if "#captcha" not in s:
        continue
    m = LOOP_RE.search(s)
    if m:
        user_expr, pass_expr, cap_expr = m.group(3), m.group(4), m.group(5)
        repl = (f'await setVal("#username", {user_expr});\n'
                f'    await setVal("#password", {pass_expr});\n'
                f'    await setVal("#captcha", {cap_expr});\n')
        s = s[:m.start()] + repl + s[m.end():]
        if "async function setVal" not in s:
            s = s[:m.start()] + HELPER + s[m.start():]
        f.write_text(s, encoding="utf-8")
        patched.append(f.name)

print("patched:", patched or "none")
