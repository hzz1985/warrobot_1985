"""RealLern v3 — persist every buy parameter so the 14-day data can calibrate them.

Adds two sheets:
  پارامترها       : current value of every buy/sell/risk parameter
  کاندید-پارامتر  : long format (signal_date, symbol, key, value) for each candidate
"""
from pathlib import Path

P = Path(r"C:\Users\Administrator\Desktop\DeepSeek DeskTop\TeseRobot\scripts\reallern_tracker.py")
s = P.read_text(encoding="utf-8")

PARAM_BLOCK = r'''

PARAM_KEYS = [
    ("buy.enabled", "خرید خودکار روشن/خاموش"),
    ("buy.preopen_attempt", "ساعت تلاش سرخطی پیش‌گشایش"),
    ("buy.window_start", "شروع پنجرهٔ تلاش پیوسته"),
    ("buy.window_end", "پایان پنجرهٔ تلاش پیوسته"),
    ("buy.interval_sec", "فاصلهٔ تلاش‌ها (ثانیه)"),
    ("buy.entry_price_mode", "مبنای قیمت ورود"),
    ("buy.min_score", "حداقل امتیاز قبولی"),
    ("buy.preopen_queue.enabled", "فیلتر صف پیش‌گشایش فعال"),
    ("buy.preopen_queue.max_dist_to_ceiling_pct", "حداکثر فاصله تا سقف (٪)"),
    ("buy.preopen_queue.min_queue_value_rial", "حداقل ارزش صف (ریال)"),
    ("buy.preopen_queue.min_imbalance", "حداقل عدم‌تعادل خرید/فروش"),
    ("buy.preopen_queue.require_no_ask_at_ceiling", "الزام نبود عرضه روی سقف"),
    ("signals.source", "منبع کاندیدها"),
    ("ride.min_prob", "حداقل احتمال مدل دوام"),
    ("ride.top_n", "حداکثر تعداد کاندید در روز"),
    ("sell.target_pct", "حد سود (٪ خالص)"),
    ("sell.stop_pct", "حد ضرر (٪ خالص)"),
    ("sell.price_mode", "مبنای قیمت فروش"),
    ("sell.retry_cooldown_sec", "کول‌داون تلاش مجدد فروش"),
    ("portfolio.max_positions", "حداکثر تعداد پوزیشن"),
    ("risk.max_daily_buys", "سقف خرید روزانه"),
    ("risk.max_orders_per_symbol_per_day", "سقف سفارش هر نماد در روز"),
    ("fees.buy_pct", "کارمزد خرید (٪)"),
    ("fees.sell_pct", "کارمزد فروش (٪)"),
    ("system.clock_offset_sec", "اصلاح ساعت (ثانیه)"),
]


def param_rows(cfg) -> list:
    out = []
    for key, desc in PARAM_KEYS:
        try:
            val = cfg.get(key)
        except Exception:
            val = None
        out.append([key, "" if val is None else str(val), desc])
    return out


def add_params_sheet(wb, cfg) -> None:
    ws = wb.create_sheet("پارامترها")
    ws.sheet_view.rightToLeft = True
    ws.append(["پارامتر", "مقدار فعلی", "توضیح"])
    for c in ws[1]:
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor="1F4E78")
    for r in param_rows(cfg):
        ws.append(r)
    ws.column_dimensions["A"].width = 44
    ws.column_dimensions["B"].width = 18
    ws.column_dimensions["C"].width = 40


def add_candidate_params_sheet(wb, rows: list, cfg) -> None:
    """Long format so the 14-day data can be filtered by any parameter."""
    ws = wb.create_sheet("کاندید-پارامتر")
    ws.sheet_view.rightToLeft = True
    ws.append(["تاریخ سیگنال", "نماد", "پارامتر", "مقدار"])
    for c in ws[1]:
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor="1F4E78")
    params = param_rows(cfg)
    for r in rows:
        sig, sym = r[0], r[1]
        for key, val, _desc in params:
            ws.append([sig, sym, key, val])
    ws.column_dimensions["A"].width = 14
    ws.column_dimensions["B"].width = 10
    ws.column_dimensions["C"].width = 46
    ws.column_dimensions["D"].width = 18
'''

if "PARAM_KEYS = [" not in s:
    s = s.replace("def write_workbook(", PARAM_BLOCK + "\n\ndef write_workbook(", 1)

# call the two new sheets from main (write_workbook has no cfg, so pass it)
s = s.replace("def write_workbook(rows: list) -> None:", "def write_workbook(rows: list, cfg=None) -> None:")
s = s.replace("    add_summary(wb, rows)\n    wb.save(XLSX)",
              "    add_summary(wb, rows)\n    if cfg is not None:\n        add_params_sheet(wb, cfg)\n        add_candidate_params_sheet(wb, rows, cfg)\n    wb.save(XLSX)")
s = s.replace("    write_workbook(rows)", "    write_workbook(rows, cfg)")

P.write_text(s, encoding="utf-8")
print("RealLern v3 patched")
