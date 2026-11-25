"""Wire the three readiness emails (08:30 / 08:40 / 08:45) into the scheduler."""
from pathlib import Path

p = Path(r"C:\Users\Administrator\Desktop\DeepSeek DeskTop\WarRobot\scheduler.py")
s = p.read_text(encoding="utf-8")

old = '''JOBS = [
    ("08:40", "preflight", ["preflight.py"]),
    ("08:45", "order", ["order_candidates.py"]),'''
new = '''JOBS = [
    ("08:30", "ready30", ["ready_check.py"]),
    ("08:40", "ready40", ["ready_check.py"]),
    ("08:40", "preflight", ["preflight.py"]),
    # the order must fire at 08:45:01 before the 08:45 readiness email
    ("08:45", "order", ["order_candidates.py"]),
    ("08:45", "ready45", ["ready_check.py"]),'''
if old in s:
    s = s.replace(old, new, 1)
    p.write_text(s, encoding="utf-8")
    print("three readiness emails wired")
else:
    print("JOBS block not found — current content:")
    i = s.find("JOBS = [")
    print(s[i:i+400])
