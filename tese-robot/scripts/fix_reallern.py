"""Fix RealLern row building: match the candidate to its session by close price
(the log stores a Gregorian timestamp while the data is Jalali) and use the
Jalali session date in the sheet."""
from pathlib import Path

P = Path(r"C:\Users\Administrator\Desktop\DeepSeek DeskTop\TeseRobot\scripts\reallern_tracker.py")
s = P.read_text(encoding="utf-8")

start = s.index("def build_rows(")
end = s.index("def write_workbook(")

new = '''def build_rows(cands: pd.DataFrame, daily: dict) -> list:
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
        # the session the candidate refers to = the last row whose close matches
        idx = None
        for i in range(len(closes) - 1, -1, -1):
            if abs(closes[i] - target) < max(1.0, target * 0.001):
                idx = i
                break
        if idx is None:
            continue
        sig_date = dates[idx]
        base = [sig_date, sym, round(float(c.get("prob") or 0), 3), reason_text(c),
                round((float(c.get("ceiling_today") or 0) - target) / max(target, 1) * 100, 3),
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


'''

s = s[:start] + new + s[end:]
P.write_text(s, encoding="utf-8")
print("build_rows rewritten")
