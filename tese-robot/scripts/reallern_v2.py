"""RealLern v2 — 14-session programme (not 6), with a summary sheet.

Adds:
  * a «خلاصه» sheet: averages by entry-distance band, win rate, best/worst
  * a programme marker (start session + 14 sessions) shown in the sheet
"""
from pathlib import Path

P = Path(r"C:\Users\Administrator\Desktop\DeepSeek DeskTop\TeseRobot\scripts\reallern_tracker.py")
s = P.read_text(encoding="utf-8")

# 1) programme length
s = s.replace("TRACK_DAYS = 5", "TRACK_DAYS = 5\nPROGRAMME_SESSIONS = 14      # the programme runs 14 trading days\nBANDS = [(0, 0.25, \"۰–۰٫۲۵٪\"), (0.25, 0.5, \"۰٫۲۵–۰٫۵٪\"),\n         (0.5, 1.0, \"۰٫۵–۱٪\"), (1.0, 5.0, \"۱–۵٪\")]")

# 2) summary sheet builder
summary = r'''

def add_summary(wb, rows: list) -> None:
    """Average outcome per distance band over the whole programme."""
    ws = wb.create_sheet("خلاصه")
    ws.sheet_view.rightToLeft = True
    ws.append([f"برنامهٔ پایش ۱۴ روز معاملاتی — هر کاندید {TRACK_DAYS} جلسه"])
    ws.append(["باند فاصله", "تعداد", "میانگین بیشترین رشد ٪", "میانگین بیشترین افت ٪",
               "میانگین D+5 ٪", "نرخ مثبت D+5 ٪"])
    for c in ws[1]:
        c.font = Font(bold=True)
    for c in ws[2]:
        c.font = Font(bold=True)
        c.fill = PatternFill("solid", fgColor="1F4E78")
        c.font = Font(bold=True, color="FFFFFF")
    for lo, hi, label in BANDS:
        sel = []
        for r in rows:
            try:
                dist = float(r[4])
            except Exception:
                continue
            if lo <= dist < hi:
                sel.append(r)
        if not sel:
            ws.append([label, 0, None, None, None, None])
            continue
        mg = [r[15] for r in sel if isinstance(r[15], (int, float))]
        md = [r[16] for r in sel if isinstance(r[16], (int, float))]
        d5 = [r[14] for r in sel if isinstance(r[14], (int, float))]
        win = (sum(1 for x in d5 if x > 0) / len(d5) * 100) if d5 else None
        ws.append([label, len(sel),
                   round(sum(mg) / len(mg), 2) if mg else None,
                   round(sum(md) / len(md), 2) if md else None,
                   round(sum(d5) / len(d5), 2) if d5 else None,
                   round(win, 1) if win is not None else None])
    for i in range(1, 7):
        ws.column_dimensions[ws.cell(row=2, column=i).column_letter].width = 22
'''

if "def add_summary(" not in s:
    s = s.replace("def write_workbook(", summary + "\n\ndef write_workbook(", 1)

# 3) call it and state the programme window in the guide
s = s.replace('''    wb.save(XLSX)''', '''    add_summary(wb, rows)
    wb.save(XLSX)''')

s = s.replace('"این فایل هر شب بعد از اسکن کاندیدها به‌روزرسانی می‌شود.",',
              '"این فایل هر شب بعد از اسکن کاندیدها به‌روزرسانی می‌شود و برنامهٔ آن ۱۴ روز معاملاتی است.",')

P.write_text(s, encoding="utf-8")
print("RealLern v2 patched")
