"""Add an equity-history snapshot and feed the P/L curve from the broker numbers.

1) pnl.py: after each build, append/update TODAY's snapshot
     state/equity_history.jsonl  {date, at, holdings_value, cash, total, pnl_rial, pnl_pct}
2) warrobot_data.chart_points(): return the equity history (cumulative P/L over
   time) when there are >= 2 days, otherwise the per-symbol P/L from the broker
   so the curve is never empty or stale.
"""
from pathlib import Path

WAR = Path(r"C:\Users\Administrator\Desktop\DeepSeek DeskTop\WarRobot")
WEB = WAR.parent / "TeseRobot" / "webapp"

# ---------------------------------------------------------------- 1) pnl.py
p = WAR / "pnl.py"
s = p.read_text(encoding="utf-8")
if "equity_history" not in s:
    s = s.replace('''    out = {"at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),''',
'''    _append_equity(rows)
    out = {"at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),''')
    s = s.replace('''def build() -> dict:''',
'''HISTORY = BASE / "state" / "equity_history.jsonl"


def _cash() -> float:
    """Free+blocked cash from the broker snapshot when available."""
    try:
        d = json.loads((BASE / "state" / "broker_portfolio.json").read_text(encoding="utf-8"))
        for key in ("balance", "cash"):
            if isinstance(d.get(key), (int, float)):
                return float(d[key])
    except Exception:
        pass
    return 0.0


def _append_equity(rows: list[dict]) -> None:
    """One line per trading day: the portfolio value and its total P/L."""
    today = datetime.now().strftime("%Y-%m-%d")
    holdings = sum(float(r.get("value") or 0) for r in rows)
    pnl_rial = sum(float(r.get("pnl_rial") or 0) for r in rows)
    invested = sum(float(r.get("invested") or 0) for r in rows)
    rec = {"date": today, "at": datetime.now().strftime("%H:%M"),
           "holdings_value": round(holdings, 0), "cash": round(_cash(), 0),
           "total": round(holdings + _cash(), 0),
           "pnl_rial": round(pnl_rial, 0),
           "pnl_pct": round((pnl_rial / invested) * 100, 2) if invested else 0.0,
           "symbols": {r["symbol"]: round(float(r.get("pnl_rial") or 0), 0) for r in rows}}
    lines = []
    try:
        lines = HISTORY.read_text(encoding="utf-8").splitlines()
    except Exception:
        lines = []
    out, replaced = [], False
    for ln in lines:
        ln = ln.strip()
        if not ln:
            continue
        try:
            d = json.loads(ln)
        except Exception:
            continue
        if d.get("date") == today:
            out.append(json.dumps(rec, ensure_ascii=False))
            replaced = True
        else:
            out.append(ln)
    if not replaced:
        out.append(json.dumps(rec, ensure_ascii=False))
    HISTORY.write_text("\\n".join(out[-400:]) + "\\n", encoding="utf-8")


def build() -> dict:''')
    p.write_text(s, encoding="utf-8")
    print("pnl.py: equity history added")
else:
    print("pnl.py already writes history")

# ------------------------------------------------------ 2) dashboard chart data
a = WEB / "warrobot_data.py"
w = a.read_text(encoding="utf-8")
if "equity_history" not in w:
    start = w.find("def chart_points()")
    end = w.find("\n", w.find("return out", start))
    new = '''def chart_points() -> list[dict]:
    """Points for svg_chart: the real portfolio P/L over time.

    Preferred: state/equity_history.jsonl (one line per trading day with the
    broker's own P/L). Fallback: per-symbol P/L from state/pnl.json.
    """
    hist = STATE / "equity_history.jsonl"
    pts = []
    if hist.exists():
        try:
            for line in hist.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                d = json.loads(line)
                pts.append({"symbol": "portfolio", "ts_fa": d.get("date", ""),
                            "invested": 0, "proceeds": 0,
                            "pnl_rial": float(d.get("pnl_rial") or 0)})
        except Exception:
            pts = []
    if len(pts) >= 2:
        return pts
    snap = _pnl_snapshot()
    for p in snap:
        pts.append({"symbol": p["symbol"], "ts_fa": (p.get("avg_source") or ""),
                    "invested": p["invested"], "proceeds": p["value"],
                    "pnl_rial": p["total_pnl"]})
    return pts
'''
    w = w[:start] + new + w[end:]
    a.write_text(w, encoding="utf-8")
    print("dashboard chart now uses the equity history")
else:
    print("dashboard chart already wired")
