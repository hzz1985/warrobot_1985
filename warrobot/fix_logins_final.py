"""Final login sweep: set username/password/captcha in ONE evaluate call.

This covers every remaining variant of the click+type login block:
    for (const [sel, val] of [["#username", CREDS.user], ...]) { ... }
which crashed with "page.click: Timeout" whenever an input was not clickable.
"""
from __future__ import annotations

import re
from pathlib import Path

PW = Path(r"C:\Users\Administrator\Desktop\DeepSeek DeskTop\WarRobot\pw")

PATTERN = re.compile(
    r'for \(const \[sel, val\] of \[\s*\["#username",\s*CREDS\.user\],\s*'
    r'\["#password",\s*CREDS\.password\],\s*\["#captcha",\s*(\w+)\]\s*\]\) '
    r'\{[\s\S]*?\n\s*\}'
)

REPLACEMENT = '''await page.evaluate(({ u, p, c }) => {
      const set = (sel, val) => {
        const el = document.querySelector(sel);
        if (!el) return;
        const proto = el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype
                                                        : HTMLInputElement.prototype;
        Object.getOwnPropertyDescriptor(proto, "value").set.call(el, String(val));
        el.dispatchEvent(new Event("input", { bubbles: true }));
        el.dispatchEvent(new Event("change", { bubbles: true }));
      };
      set("#username", u); set("#password", p); set("#captcha", c);
    }, { u: CREDS.user, p: CREDS.password, c: {CAPTCHA} });'''

patched = []
for f in sorted(PW.glob("*.js")):
    s = f.read_text(encoding="utf-8", errors="replace")
    m = PATTERN.search(s)
    if not m:
        continue
    s = s[:m.start()] + REPLACEMENT.replace("{CAPTCHA}", m.group(1)) + s[m.end():]
    f.write_text(s, encoding="utf-8")
    patched.append(f.name)

print("patched:", patched or "none")
