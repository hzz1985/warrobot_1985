"""Candidates must carry the session they are FOR (Saturday after a Wednesday),
and the Excel tracker must show it."""
from pathlib import Path

RIDE = Path(r"C:\Users\Administrator\Desktop\DeepSeek DeskTop\TeseRobot\src\tsebot\ride_locked.py")
s = RIDE.read_text(encoding="utf-8")

helper = '''
def next_session_label() -> str:
    """Jalali date of the next trading session (Thu/Fri closed)."""
    import jdatetime
    from datetime import timedelta
    d = jdatetime.date.today()
    for _ in range(10):
        d = d + timedelta(days=1)
        if d.weekday() not in (5, 6):          # 5=Thursday, 6=Friday
            return d.strftime("%Y-%m-%d")
    return ""

'''

if "def next_session_label" not in s:
    s = s.replace("def write_candidates(", helper + "\ndef write_candidates(", 1)

s = s.replace('"for_session": "next", "built_at"', '"for_session": next_session_label(), "built_at"')
RIDE.write_text(s, encoding="utf-8")

TRACK = Path(r"C:\Users\Administrator\Desktop\DeepSeek DeskTop\TeseRobot\scripts\reallern_tracker.py")
t = TRACK.read_text(encoding="utf-8")
t = t.replace('HEADERS = ["تاریخ سیگنال", "نماد",', 'HEADERS = ["تاریخ سیگنال", "جلسهٔ هدف", "نماد",')
t = t.replace('''        base = [sig_date, sym, round(float(c.get("prob") or 0), 3), reason_text(c),''',
              '''        base = [sig_date, str(c.get("for_session") or "-"), sym,
                round(float(c.get("prob") or 0), 3), reason_text(c),''')
t = t.replace('"قیمت بسته", "سقف روز", "حد نوسان ٪", "سقف فردا (تخمین)",\n           "قیمت ورود (بازگشایی)"',
              '"قیمت بسته", "سقف روز", "حد نوسان ٪", "سقف جلسهٔ بعد (تخمین)",\n           "قیمت ورود (بازگشایی)"')
TRACK.write_text(t, encoding="utf-8")
print("patched for_session + tracker column")
