"""Web app: remove the DeepSeek tab completely + light (white) theme.

The dashboard had three tabs; the DeepSeekExport archive is removed from the
page (section + tab button) without touching the data files, and a light theme
layer replaces the dark one.
"""
from pathlib import Path

WEB = Path(r"C:\Users\Administrator\Desktop\DeepSeek DeskTop\TeseRobot\webapp")
tpl = WEB / "templates" / "index.html"
s = tpl.read_text(encoding="utf-8", errors="surrogateescape")

# ---------------------------------------------------------- 1) drop the tab
if 'data-tab="export"' in s:
    # remove the tab button
    import re
    s = re.sub(r'<[^>]*data-tab="export"[^>]*>.*?</[^>]+>\s*', "", s, count=1, flags=re.S)
# remove the export panel section
if 'id="export"' in s:
    start = s.find('<section class="panel" id="export">')
    if start == -1:
        start = s.find('<section class="panel" id="export"')
    if start != -1:
        end = s.find("</section>", start)
        if end != -1:
            s = s[:start] + s[end + len("</section>"):]
print("export tab removed:", 'data-tab="export"' not in s and 'id="export"' not in s)

# ------------------------------------------------- 2) light theme override
if "LIGHT-THEME" not in s:
    light = """<style>
/* ===== LIGHT-THEME 2026 — white dashboard ===== */
:root{
  --bg:#f6f8fb; --card:#ffffff; --card2:#fbfdff; --line:#e3e9f0; --line2:#d3ddeb;
  --txt:#16202b; --muted:#5d6b7a; --accent:#1f6feb; --accent2:#3b82f6;
  --green:#14804a; --red:#c62828; --amber:#b26a00;
  --shadow:0 4px 18px rgba(20,40,80,.08); --radius:14px;
}
html,body{background:#f6f8fb !important; color:#16202b !important}
body{
  background:
    radial-gradient(1100px 500px at 90% -10%, rgba(59,130,246,.08), transparent 60%),
    radial-gradient(800px 400px at 5% 0%, rgba(20,128,74,.05), transparent 55%),
    #f6f8fb !important;
}
header{
  background:rgba(255,255,255,.92) !important; border-bottom:1px solid #e3e9f0 !important;
  box-shadow:0 2px 10px rgba(20,40,80,.06) !important;
}
h1{background:none !important; -webkit-text-fill-color:#0f1a26 !important; color:#0f1a26 !important}
.badge{background:#eef3f9 !important; color:#48606f !important; border:1px solid #dbe4ee !important}
.badge.live{background:#fdecec !important; color:#a11616 !important; border-color:#f3c9c9 !important}
.badge.dry{background:#e9f7ef !important; color:#11643c !important; border-color:#bfe6d0 !important}
.tabs{background:linear-gradient(#f6f8fb, rgba(246,248,251,0)) !important}
.tab{background:#edf2f8 !important; color:#4b5d6d !important; border:1px solid #dbe4ee !important}
.tab.active{background:#ffffff !important; color:#0f1a26 !important;
             box-shadow:inset 0 -2px 0 var(--accent) !important}
.panel{background:transparent !important}
.kpi{background:#ffffff !important; border:1px solid #e3e9f0 !important; box-shadow:var(--shadow) !important}
.kpi .lbl{color:#5d6b7a !important}
.kpi .val{color:#10202e !important}
table{background:#ffffff !important; border-color:#e3e9f0 !important}
thead th{background:#f1f5fa !important; color:#33475b !important; border-bottom:1px solid #e3e9f0 !important}
td,th{color:#22303c !important; border-bottom:1px solid #eef2f7 !important}
tbody tr:hover{background:#f5f9ff !important}
h2{color:#0f1a26 !important}
h2::before{background:linear-gradient(180deg,#3b82f6,#1f6feb) !important}
.note,p,.empty{color:#5d6b7a !important}
.pos{color:#14804a !important}
.neg{color:#c62828 !important}
a{color:#1f6feb !important}
canvas{filter:none !important}
.text, .chart-legend{color:#33475b !important}
</style>
"""
    s = s.replace("</head>", light + "</head>", 1)
tpl.write_text(s, encoding="utf-8", errors="surrogateescape")
print("light theme applied:", "LIGHT-THEME" in s)
print("template bytes:", tpl.stat().st_size)
