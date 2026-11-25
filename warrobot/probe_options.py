"""Probe: can we reach Tehran derivatives (options) instruments and history?"""
import glob
import inspect
import sys

CANDIDATES = [
    r"C:\Users\Administrator\Desktop\DeepSeek DeskTop\TeseRobot\src",
    r"C:\Users\Administrator\Desktop\DeepSeek DeskTop\TeseRobot",
    r"C:\Users\Administrator\Desktop\DeepSeek DeskTop\Tse2\orderflow",
]
for p in CANDIDATES:
    sys.path.insert(0, p)

mods = []
for name in ("tsetmc", "tsebot.tsetmc", "tsebot.api"):
    try:
        m = __import__(name, fromlist=["*"])
        mods.append((name, m))
    except Exception as exc:
        print(f"{name}: import failed ({str(exc)[:60]})")

for name, m in mods:
    fns = [n for n, o in inspect.getmembers(m, inspect.isfunction)]
    print(f"\n=== {name} ({getattr(m, '__file__', '?')}) ===")
    print("functions:", fns[:40])

# search the source tree for derivative-related identifiers
print("\n=== grep derivative identifiers ===")
import re
hits = {}
for path in glob.glob(r"C:\Users\Administrator\Desktop\DeepSeek DeskTop\TeseRobot\**\*.py", recursive=True)[:400]:
    try:
        txt = open(path, encoding="utf-8", errors="ignore").read()
    except Exception:
        continue
    for kw in ("derivative", "Derivative", "option", "Option", "مشتقه", "آپشن", "tablaval", "instrument"):
        if kw in txt:
            hits.setdefault(kw, []).append(path.split("\\")[-1])
for k, v in hits.items():
    print(k, "->", sorted(set(v))[:6])
