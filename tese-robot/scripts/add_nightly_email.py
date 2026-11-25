"""Nightly email: data download finished + tomorrow's buy candidates."""
from pathlib import Path

R = Path(r"C:\Users\Administrator\Desktop\DeepSeek DeskTop\TeseRobot")

# 1) add the alert function to alerts.py
ALERTS = R / "src" / "tsebot" / "alerts.py"
a = ALERTS.read_text(encoding="utf-8")

FN = r'''

def alert_nightly_summary(rows, session_label: str = "", stats: dict | None = None) -> bool:
    """Email after the nightly data refresh: download status + next-session candidates."""
    stats = stats or {}
    lines = []
    lines.append("دانلود دادهٔ شبانه با موفقیت و به‌طور کامل انجام شد.")
    lines.append("")
    lines.append(f"تاریخ اجرا: {now_tehran().strftime('%Y-%m-%d %H:%M')} (تهران)")
    if stats.get("last_date"):
        lines.append(f"آخرین جلسهٔ موجود در داده: {stats['last_date']}")
    if stats.get("symbols"):
        lines.append(f"نمادهای به‌روزشده: {stats['symbols']}")
    if session_label:
        lines.append(f"جلسهٔ هدف خرید: {session_label}")
    lines.append("")
    if rows:
        lines.append("کاندیداهای خرید جلسهٔ بعد:")
        lines.append("")
        lines.append(f"{'#':<3}{'نماد':<10}{'احتمال':>8}{'فاصله تا سقف':>14}{'قیمت بسته':>12}{'سقف بعد':>12}")
        for i, r in enumerate(rows, 1):
            lines.append(f"{i:<3}{r.get('symbol',''):<10}{float(r.get('prob') or 0):>8.3f}"
                         f"{float(r.get('dist_pct') or 0):>13.2f}%"
                         f"{float(r.get('close') or 0):>12,.0f}"
                         f"{float(r.get('next_ceiling') or 0):>12,.0f}")
        lines.append("")
        lines.append("سفارش‌گذاری خودکار: ۰۸:۴۵ (سرخطی) و ۰۹:۰۰ تا ۰۹:۰۵ هر ۳۰ ثانیه.")
    else:
        lines.append("هیچ کاندیدای واجد شرایطی برای جلسهٔ بعد پیدا نشد.")
    body = "\n".join(lines)
    return send(f"[TeseRobot] دانلود شبانه کامل شد — {session_label or 'کاندیدهای فردا'}",
                body, key=f"nightly:{session_label}", force=True)
'''

if "def alert_nightly_summary(" not in a:
    a = a.rstrip() + "\n" + FN
    ALERTS.write_text(a, encoding="utf-8")
    print("alert added to alerts.py")

# 2) call it from the picks task after the nightly scan
S = R / "src" / "tsebot" / "scheduler.py"
s = S.read_text(encoding="utf-8")
old = """            try:
                df = ride_locked.write_candidates(self.cfg)
                self.log(f"ride candidates for next session: "
                         f"{[(r['symbol'], r['prob']) for _, r in df.iterrows()]}")
            except Exception as exc:
                self.log(f"ride candidates failed: {exc}")
            return"""
new = """            try:
                df = ride_locked.write_candidates(self.cfg)
                self.log(f"ride candidates for next session: "
                         f"{[(r['symbol'], r['prob']) for _, r in df.iterrows()]}")
                # nightly email: download finished + the candidate list
                try:
                    from .alerts import alert_nightly_summary
                    from .calendar_ir import TradingCalendar
                    rows = []
                    for _, r in df.iterrows():
                        close = float(r.get("close") or 0)
                        ceil = float(r.get("ceiling_today") or 0)
                        rows.append({"symbol": r["symbol"], "prob": r.get("prob"),
                                     "dist_pct": ((ceil - close) / close * 100) if close else 0,
                                     "close": close,
                                     "next_ceiling": r.get("next_ceiling_est")})
                    cal = TradingCalendar()
                    last = str(df["for_session"].iloc[0]) if len(df) else ""
                    stats = {"symbols": self._daily_symbols_count(),
                             "last_date": self._daily_last_date()}
                    ok = alert_nightly_summary(rows, last, stats)
                    self.log(f"nightly email sent={ok}")
                except Exception as exc:
                    self.log(f"nightly email failed: {exc}")
            except Exception as exc:
                self.log(f"ride candidates failed: {exc}")
            return"""
if old in s:
    s = s.replace(old, new, 1)
else:
    print("WARN: picks block pattern not found")

HELPERS = '''
    def _daily_last_date(self) -> str:
        try:
            import pandas as _pd
            d = self.cfg.path("daily_dir")
            if not d.exists():
                d = ROOT / "data" / "daily"
            files = list(d.glob("*_daily.csv"))
            if not files:
                return ""
            last = ""
            for f in files[:80]:
                try:
                    df = _pd.read_csv(f, usecols=["date"], dtype={"date": str})
                    if len(df):
                        last = max(last, str(df["date"].iloc[-1]))
                except Exception:
                    continue
            return last
        except Exception:
            return ""

    def _daily_symbols_count(self) -> int:
        try:
            d = self.cfg.path("daily_dir")
            if not d.exists():
                d = ROOT / "data" / "daily"
            return len(list(d.glob("*_daily.csv")))
        except Exception:
            return 0

'''
if "_daily_last_date" not in s:
    s = s.replace("    def task_picks(self) -> None:", HELPERS + "    def task_picks(self) -> None:", 1)

S.write_text(s, encoding="utf-8")
print("scheduler wired")
