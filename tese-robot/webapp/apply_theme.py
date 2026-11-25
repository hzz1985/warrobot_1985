"""Inject the professional theme into the dashboard template (idempotent)."""
from pathlib import Path

WEB = Path(r"C:\Users\Administrator\Desktop\DeepSeek DeskTop\TeseRobot\webapp")
tpl = WEB / "templates" / "index.html"
css = WEB / "templates" / "_theme_pro.css.html"

s = tpl.read_text(encoding="utf-8", errors="surrogateescape")
block = css.read_text(encoding="utf-8", errors="surrogateescape")

if "PRO-THEME" in s:
    print("theme already applied")
else:
    s = s.lstrip("\ufeff")
    s = s.replace("</head>", block + "\n</head>", 1)
    tpl.write_text(s, encoding="utf-8", errors="surrogateescape")
    print("professional theme injected")
print("template bytes:", tpl.stat().st_size)
