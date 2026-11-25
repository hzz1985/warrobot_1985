"""Accurate login detection: never type the password into an already-logged-in panel.

Symptom reported by the operator: he is logged in inside Chrome, yet the bot opens
its own browser and still tries to log in (captcha window pops up).

Cause: the check used `el.offsetParent !== null`, which is TRUE for the hidden
login form that the SPA keeps in the DOM on /Home/Default/page-1. So every run
"thought" it had to log in.

Fix: a login is needed only when the URL is the login page AND the username field
has a real box (rect size > 0) and is visible. Applied to every pw script.
"""
from __future__ import annotations

import re
from pathlib import Path

PW = Path(r"C:\Users\Administrator\Desktop\DeepSeek DeskTop\WarRobot\pw")

ROBUST = '''async function loginFormVisible() {
  return await page.evaluate(() => {
    if (!/login/i.test(location.href)) return false;
    const u = document.querySelector("#username");
    if (!u) return false;
    const r = u.getBoundingClientRect();
    const st = window.getComputedStyle(u);
    return r.width > 0 && r.height > 0 && st.visibility !== "hidden" && st.display !== "none";
  });
}'''

INLINE_OLD = '''  const needs = await page.evaluate(() => {
    const u = document.querySelector("#username");
    return !!(u && u.offsetParent !== null);
  });'''
INLINE_NEW = '''  const needs = await page.evaluate(() => {
    if (!/login/i.test(location.href)) return false;
    const u = document.querySelector("#username");
    if (!u) return false;
    const r = u.getBoundingClientRect();
    const st = window.getComputedStyle(u);
    return r.width > 0 && r.height > 0 && st.visibility !== "hidden" && st.display !== "none";
  });'''

patched = []
for f in sorted(PW.glob("*.js")):
    s = f.read_text(encoding="utf-8", errors="replace")
    before = s
    # replace existing helper implementation
    s = re.sub(
        r"async function loginFormVisible\(\) \{[\s\S]*?\n\}", ROBUST, s, count=1)
    s = s.replace(INLINE_OLD, INLINE_NEW)
    # some scripts test the URL directly
    s = s.replace('if (page.url().includes("Login")) {', 'if (await loginFormVisible()) {')
    if s != before:
        if "async function loginFormVisible" not in s and "async function setVal" in s:
            s = s.replace("async function setVal", ROBUST + "\nasync function setVal", 1)
        f.write_text(s, encoding="utf-8")
        patched.append(f.name)

print("patched:", patched or "none")
