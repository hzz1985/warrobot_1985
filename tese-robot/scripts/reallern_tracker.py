"""RealLern — track every buy candidate for 5 sessions and record it in Excel.

For each nightly candidate we store why it was picked, its distance to the ceiling,
the entry price (next open) and then follow it for D+1 .. D+5 sessions:
    gain_pct(d)   = (close_d / entry - 1) * 100
    max_gain      = highest close within the window vs entry
    max_drawdown  = lowest close within the window vs entry
A candidate is created the night it is announced and updated every night afterwards.

Output: Tse/RealLern/RealLern.xlsx  (sheets: کاندیدها, عملکرد روزانه)
"""
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pandas as pd  # noqa: E402
from openpyxl import Workbook, load_workbook  # noqa: E402
from openpyxl.styles import Alignment, Font, PatternFill  # noqa: E402

from tsebot import config as config_mod  # noqa: E402

OUT_DIR = Path(r"C:\Users\Administrator\Desktop\DeepSeek DeskTop\Tse\RealLern")
XLSX = OUT_DIR / "RealLern.xlsx"
TRACK_DAYS = 5
PROGRAMME_SESSIONS = 14      # the programme runs 14 trading days
BANDS = [(0, 0.25, "۰–۰٫۲۵٪"), (0.25, 0.5, "۰٫۲۵–۰٫۵٪"),
         (0.5, 1.0, "۰٫۵–۱٪"), (1.0, 5.0, "۱–۵٪")]
HEADERS = ["تاریخ سیگنال", "جلسهٔ هدف", "نماد", "احتمال مدل", "علت کاندید", "فاصله تا سقف ٪",
           "قیمت بسته", "سقف روز", "حد نوسان ٪", "سقف جلسهٔ بعد (تخمین)",
           "قیمت ورود (بازگشایی)", "D+1", "D+2", "D+3", "D+4", "D+5",
           "بیشترین رشد ٪", "بیشترین افت ٪", "وضعیت"]


def load_daily(cfg) -> dict:
    d = Path(str(cfg.path("daily_dir")))
    if not d.exists():
        d = Path(__file__).resolve().parents[1] / "data" / "daily"
    out = {}
    for f in d.glob("*_daily.csv"):
        sym = f.name[:-len("_daily.csv")]
        try:
            df = pd.read_csv(f, dtype={"date": str}).sort_values("date").reset_index(drop=True)
        except Exception:
            continue
        out[sym] = df
    return out


def read_candidates(cfg) -> pd.DataFrame:
    p = cfg.path("state_dir") / "candidates_history.csv"
    if not p.exists():
        return pd.DataFrame()
    df = pd.read_csv(p, dtype={"built_at": str, "logged_at": str})
    df["signal_date"] = df["built_at"].str[:10]
    return df.drop_duplicates(["symbol", "for_session"])


def target_session(sig_date: str) -> str:
    """Next trading session (Thu/Fri closed) after the signal date, Jalali string."""
    import jdatetime
    from datetime import timedelta
    try:
        d = jdatetime.datetime.strptime(sig_date, "%Y-%m-%d").date()
    except Exception:
        return str(sig_date)
    for _ in range(10):
        d = d + timedelta(days=1)
        if d.weekday() not in (5, 6):
            return str(d)
    return str(sig_date)


def reason_text(row) -> str:
    bits = []
    close, ceil = float(row.get("close") or 0), float(row.get("ceiling_today") or 0)
    if ceil and close >= ceil * 0.999:
        bits.append("قفل روی سقف")
    dist = ((ceil - close) / close * 100) if close else 99
    if dist <= 0.25:
        bits.append("فاصله <۰٫۲۵٪")
    elif dist <= 0.5:
        bits.append("فاصله <۰٫۵٪")
    bits.append(f"احتمال {float(row.get('prob') or 0):.3f}")
    return " · ".join(bits)


def build_rows(cands: pd.DataFrame, daily: dict) -> list:
    rows = []
    for _, c in cands.iterrows():
        sym = str(c["symbol"])
        d = daily.get(sym)
        if d is None or d.empty:
            continue
        closes = d["Close"].astype(float).to_numpy()
        opens = d["Open"].astype(float).to_numpy()
        dates = d["date"].tolist()
        target = float(c.get("close") or 0)
        # the signal session: convert the logged Gregorian timestamp to Jalali and
        # match the daily row by DATE (price matching picked the wrong older session
        # when two sessions closed at the same price)
        sig_date = ""
        try:
            import jdatetime
            from datetime import datetime as _dt
            from zoneinfo import ZoneInfo
            # built_at is the LOCAL machine time: convert it to Tehran before taking
            # the session date, otherwise an evening scan lands on the wrong session
            local_tz = _dt.now().astimezone().tzinfo
            ts = _dt.fromisoformat(str(c.get("built_at"))[:19]).replace(tzinfo=local_tz)
            teh = ts.astimezone(ZoneInfo("Asia/Tehran")).date()
            sig_date = jdatetime.date.fromgregorian(date=teh).strftime("%Y-%m-%d")
        except Exception:
            sig_date = ""
        # the scan may run before the session's data is downloaded, so the signal
        # session is the LAST row (not after the scan date) whose close matches
        idx = None
        for i in range(len(closes) - 1, -1, -1):
            if dates[i] > sig_date:
                continue
            if abs(closes[i] - target) < max(1.0, target * 0.001):
                idx = i
                break
        if idx is None:
            continue
        # if the symbol's own data is stale (its file has no bar for the scan session)
        # the close match would land on an old session: skip it instead of mislabelling
        if (len(closes) - 1 - idx) > 2:
            continue
        sig_date = dates[idx]
        base = [sig_date, target_session(sig_date), sym,
                round(float(c.get("prob") or 0), 3), reason_text(c),
                round(max((float(c.get("ceiling_today") or 0) - target) / max(target, 1) * 100, 0.0), 3),
                target, float(c.get("ceiling_today") or 0), float(c.get("limit_pct") or 0),
                float(c.get("next_ceiling_est") or 0)]
        if idx + 1 >= len(closes):
            rows.append(base + [None, None, None, None, None, None, None, None, "در انتظار بازگشایی"])
            continue
        entry = float(opens[idx + 1])
        gains = []
        for k in range(1, TRACK_DAYS + 1):
            j = idx + k
            gains.append(round((closes[j] / entry - 1) * 100, 2) if j < len(closes) else None)
        have = [g for g in gains if g is not None]
        max_g = round(max(have), 2) if have else None
        max_d = round(min(have), 2) if have else None
        status = "کامل" if len(have) == TRACK_DAYS else f"در حال پایش ({len(have)}/{TRACK_DAYS})"
        rows.append(base + [round(entry, 1)] + gains + [max_g, max_d, status])
    return rows




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


def write_workbook(rows: list, cfg=None) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    wb = Workbook()
    ws = wb.active
    ws.title = "کاندیدها"
    ws.sheet_view.rightToLeft = True
    head_fill = PatternFill("solid", fgColor="1F4E78")
    ws.append(HEADERS)
    for cell in ws[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = head_fill
        cell.alignment = Alignment(horizontal="center", vertical="center")
    for r in rows:
        ws.append(r)
    widths = [12, 10, 11, 30, 12, 11, 11, 9, 13, 14, 8, 8, 8, 8, 8, 12, 12, 16]
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[ws.cell(row=1, column=i).column_letter].width = w
    ws.freeze_panes = "A2"

    ws2 = wb.create_sheet("راهنما")
    ws2.sheet_view.rightToLeft = True
    for line in [
        "این فایل هر شب بعد از اسکن کاندیدها به‌روزرسانی می‌شود و برنامهٔ آن ۱۴ روز معاملاتی است.",
        "هر کاندید از روز بعد از سیگنال، ۵ جلسه پایش می‌شود (D+1 تا D+5).",
        "ستون D+n = درصد تغییر قیمت بسته‌شدن آن روز نسبت به قیمت ورود (بازگشایی روز اول).",
        "بیشترین رشد/افت = بهترین/بدترین مقدار در همان ۵ روز.",
        "علت کاندید: قفل روی سقف، فاصله تا سقف و احتمال مدل.",
    ]:
        ws2.append([line])
        ws2.column_dimensions["A"].width = 95
    add_summary(wb, rows)
    if cfg is not None:
        add_params_sheet(wb, cfg)
        add_candidate_params_sheet(wb, rows, cfg)
    wb.save(XLSX)


def main() -> None:
    cfg = config_mod.load()
    daily = load_daily(cfg)
    cands = read_candidates(cfg)
    rows = build_rows(cands, daily) if len(cands) else []
    write_workbook(rows, cfg)
    done = sum(1 for r in rows if r[-1] == "کامل")
    print(f"RealLern: {len(rows)} candidates ({done} complete) -> {XLSX}")


if __name__ == "__main__":
    main()


