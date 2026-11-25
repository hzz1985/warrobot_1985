"""Define loginFormVisible() in every script that calls it.

The earlier sweeps replaced the URL test with `if (await loginFormVisible())` but
some scripts never received the helper definition, so they crashed with
ReferenceError (this is why the portfolio reader returned nothing and the web app
kept showing stale P&L).
"""
from __future__ import annotations

from pathlib import Path

PW = Path(r"C:\Users\Administrator\Desktop\DeepSeek DeskTop\WarRobot\pw")

HELPER = '''  async function loginFormVisible() {
    return await page.evaluate(() => {
      if (!/login/i.test(location.href)) return false;
      const u = document.querySelector("#username");
      if (!u) return false;
      const r = u.getBoundingClientRect();
      const st = window.getComputedStyle(u);
      return r.width > 0 && r.height > 0 && st.visibility !== "hidden" && st.display !== "none";
    });
  }
'''

fixed = []
for f in sorted(PW.glob("*.js")):
    s = f.read_text(encoding="utf-8", errors="replace")
    if "loginFormVisible" not in s:
        continue
    if "async function loginFormVisible" in s:
        continue
    idx = s.find("loginFormVisible()")
    if idx < 0:
        continue
    line_start = s.rfind("\n", 0, idx) + 1
    s = s[:line_start] + HELPER + s[line_start:]
    f.write_text(s, encoding="utf-8")
    fixed.append(f.name)

print("defined helper in:", fixed or "none")
