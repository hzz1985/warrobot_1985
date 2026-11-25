"""Jalali timestamps on every card + sell-tab freshness wiring."""
from pathlib import Path

W = Path(r"C:\Users\Administrator\Desktop\DeepSeek DeskTop\TeseRobot\webapp\warrobot_data.py")
s = W.read_text(encoding="utf-8")
if "def to_jalali(" not in s:
    s = s.replace('''def webdata() -> dict:''',
'''def to_jalali(ts) -> str:
    """'2026-11-18 13:32:10' -> '1405-08-27 13:32' (Persian calendar for the UI)."""
    if not ts:
        return "—"
    txt = str(ts).strip()
    try:
        from datetime import datetime as _dt
        d = _dt.fromisoformat(txt[:19])
        import jdatetime
        j = jdatetime.date.fromgregorian(date=d.date())
        return f"{j.year:04d}-{j.month:02d}-{j.day:02d} {d.strftime('%H:%M')}"
    except Exception:
        return txt


def sell_data() -> dict:
    """Sell tab payload written by the d6 data agent."""
    try:
        return json.loads((STATE / "webdata" / "sell.json").read_text(encoding="utf-8"))
    except Exception:
        return {}


def webdata() -> dict:''')
    W.write_text(s, encoding="utf-8")
    print("warrobot_data: to_jalali + sell_data")

A = Path(r"C:\Users\Administrator\Desktop\DeepSeek DeskTop\TeseRobot\webapp\app.py")
t = A.read_text(encoding="utf-8")
if "jalali=Wdata.to_jalali" not in t:
    t = t.replace("webdata=Wdata.webdata(),", "webdata=Wdata.webdata(), sell_data=Wdata.sell_data(),\n        jalali=Wdata.to_jalali,")
    A.write_text(t, encoding="utf-8")
    print("app.py passes jalali + sell_data")

H = Path(r"C:\Users\Administrator\Desktop\DeepSeek DeskTop\TeseRobot\webapp\templates\index.html")
h = H.read_text(encoding="utf-8", errors="surrogateescape")
pairs = [
    ("{{ (webdata.balance or {}).get('at') or '—' }}",
     "{{ jalali((webdata.balance or {}).get('at')) }}"),
    ("{{ (webdata.portfolio or {}).get('at') or '—' }}",
     "{{ jalali((webdata.portfolio or {}).get('at')) }}"),
    ("{{ (webdata.pnl or {}).get('at') or '—' }}",
     "{{ jalali((webdata.pnl or {}).get('at')) }}"),
    ("{{ (webdata.sells or {}).get('at') or '—' }}",
     "{{ jalali((webdata.sells or {}).get('at')) }}"),
    ("{{ pnl_at or '—' }}", "{{ jalali(pnl_at) }}"),
    ("{{ report.generated_at or '—' }}", "{{ jalali(report.generated_at) }}"),
    ("{{ p.period_end }}", "{{ jalali(p.period_end) }}"),
    ("{{ weekly.at }}", "{{ jalali(weekly.at) }}"),
    ("{{ e.at }}", "{{ jalali(e.at) }}"),
    ("{{ l.at }}", "{{ jalali(l.at) }}"),
]
changed = 0
for old, new in pairs:
    if old in h:
        h = h.replace(old, new)
        changed += 1
# sell tab: a timestamp line right above the sell candidates table
if "بروزرسانی تب فروش" not in h:
    h = h.replace('''  <h2>کاندیدهای فروش (حلقهٔ تصمیم)</h2>''',
'''  <p class="note" style="opacity:.75">بروزرسانی تب فروش: {{ jalali((sell_data or {}).get('at')) }} ·
     تصمیم‌ها: {{ (sell_data or {}).get('sell_count', 0) }} فروش ثبت‌شده ·
     میانگین سود فروش‌ها: {{ (sell_data or {}).get('avg_sell_pct') if (sell_data or {}).get('avg_sell_pct') is not none else '—' }}%</p>
  <h2>کاندیدهای فروش (حلقهٔ تصمیم)</h2>''')
    changed += 1
H.write_text(h, encoding="utf-8", errors="surrogateescape")
print("template: jalali timestamps + sell tab note (changes:", changed, ")")
