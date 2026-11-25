"""Fix the sell tab: correct recent-sell values and a chart that actually updates."""
from pathlib import Path

W = Path(r"C:\Users\Administrator\Desktop\DeepSeek DeskTop\TeseRobot\webapp\warrobot_data.py")
s = W.read_text(encoding="utf-8")

SELLS = '''def sells_history(limit: int = 5) -> list[dict]:
    """Recent closed sales — realised.jsonl and the journal are the authority."""
    rows: list[dict] = []
    seen: set[tuple] = set()

    def add(sym, at, qty, avg, price, pnl_rial, pnl_pct):
        key = (str(sym), str(at)[:19])
        if key in seen:
            return
        seen.add(key)
        rows.append({"ts": str(at)[:19], "symbol": str(sym),
                     "qty": float(qty or 0), "avg": float(avg or 0),
                     "price": float(price or 0),
                     "pnl_rial": float(pnl_rial or 0),
                     "pnl_pct": float(pnl_pct or 0)})

    try:
        for line in (STATE / "realized.jsonl").read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            d = json.loads(line)
            qty = float(d.get("qty") or 0)
            price = float(d.get("sell_price") or 0)
            avg = float(d.get("avg_cost") or 0)
            pnl = float(d.get("pnl_rial") or 0)
            pct = float(d.get("pnl_pct") or 0)
            if not pct and avg and price:
                pct = (price / avg - 1) * 100
            add(d.get("symbol"), d.get("at"), qty, avg, price, pnl, pct)
    except Exception:
        pass

    try:
        for line in (STATE / "trade_journal.jsonl").read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            d = json.loads(line)
            if d.get("status") != "closed":
                continue
            add(d.get("symbol"), d.get("sell_at"), d.get("qty"), d.get("price"),
                d.get("sell_price"), d.get("pnl_rial"), d.get("pnl_pct"))
    except Exception:
        pass

    rows.sort(key=lambda r: r["ts"], reverse=True)
    return rows[:limit]
'''


def replace_func(src: str, name: str, new_body: str) -> str:
    start = src.find(f"def {name}(")
    if start < 0:
        raise SystemExit(f"{name} not found")
    nxt = src.find("\ndef ", start + 5)
    end = nxt + 1 if nxt > 0 else len(src)
    return src[:start] + new_body + "\n" + src[end:]


s = replace_func(s, "sells_history", SELLS)

CHART = '''def chart_points() -> list[dict]:
    """Sell-tab curve: cumulative realised P/L, one point per closed sale.

    It always has a point (the آردینه stop-loss sale) so the chart is never empty,
    and it grows automatically as new sales are journaled.
    """
    pts: list[dict] = []
    try:
        for line in (STATE / "realized.jsonl").read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            d = json.loads(line)
            pts.append({"symbol": d.get("symbol", ""),
                        "ts_fa": str(d.get("at") or "")[:10],
                        "invested": float(d.get("cost_value") or 0),
                        "proceeds": float(d.get("proceeds") or 0),
                        "pnl_rial": float(d.get("pnl_rial") or 0)})
    except Exception:
        pass
    if not pts:
        try:
            for line in (STATE / "trade_journal.jsonl").read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                d = json.loads(line)
                if d.get("status") != "closed":
                    continue
                pts.append({"symbol": d.get("symbol", ""),
                            "ts_fa": str(d.get("sell_at") or "")[:10],
                            "invested": float(d.get("price") or 0) * float(d.get("qty") or 0),
                            "proceeds": float(d.get("sell_price") or 0) * float(d.get("qty") or 0),
                            "pnl_rial": float(d.get("pnl_rial") or 0)})
        except Exception:
            pts = []
    pts.sort(key=lambda p: p["ts_fa"])
    return pts
'''

s = replace_func(s, "chart_points", CHART)
W.write_text(s, encoding="utf-8")
print("sells_history + chart_points rewritten")

H = Path(r"C:\Users\Administrator\Desktop\DeepSeek DeskTop\TeseRobot\webapp\templates\index.html")
h = H.read_text(encoding="utf-8", errors="surrogateescape")
# sell tab: Jalali time + rial amount + update line
h = h.replace('''      <td data-label="زمان">{{ s.ts }}</td>''',
              '''      <td data-label="زمان">{{ jalali(s.ts) }}</td>''')
h = h.replace('''      <td data-label="سود" class="{{ 'pos' if s.pnl_pct >= 0 else 'neg' }}">{{ '%+.2f'|format(s.pnl_pct) }}%</td>''',
              '''      <td data-label="سود" class="{{ 'pos' if s.pnl_pct >= 0 else 'neg' }}">{{ '%+.2f'|format(s.pnl_pct) }}%
        <small class="rial">{{ '{:+,.0f}'.format(s.pnl_rial) }} ریال</small></td>''')
if "بروزرسانی فروش‌های اخیر" not in h:
    h = h.replace('''  <h2>فروش‌های اخیر — تجمیع هر نماد (۵ نماد آخر)</h2>''',
                  '''  <p class="note" style="opacity:.75">بروزرسانی فروش‌های اخیر: {{ jalali((sell_data or {}).get('at')) }} · منبع: realized + ژورنال</p>
  <h2>فروش‌های اخیر — تجمیع هر نماد (۵ نماد آخر)</h2>''')
H.write_text(h, encoding="utf-8", errors="surrogateescape")
print("template: sell table Jalali + rial + update line")
