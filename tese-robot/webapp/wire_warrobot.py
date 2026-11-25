"""Point the dashboard's buy/sell tabs at WarRobot's live data."""
from pathlib import Path

WEB = Path(r"C:\Users\Administrator\Desktop\DeepSeek DeskTop\TeseRobot\webapp")
app = WEB / "app.py"
s = app.read_text(encoding="utf-8", errors="surrogateescape")

# 1) import the adapter (once)
if "import warrobot_data as Wdata" not in s:
    anchor = s.find("app = Flask(")
    if anchor == -1:
        anchor = s.find("from flask import")
    s = s[:anchor] + "import warrobot_data as Wdata\n" + s[anchor:]

# 2) buy candidates
s = s.replace("    picks = current_picks()", "    picks = Wdata.picks_payload()", 1)

# 3) positions
s = s.replace(
    "    positions = [x for x in open_positions() if x[\"symbol\"] not in excluded]",
    "    positions = [x for x in Wdata.positions() if x[\"symbol\"] not in excluded]", 1)

# 4) recent sells
s = s.replace("    sells = per_symbol_series()[-5:]", "    sells = Wdata.sells_history(5)", 1)

# 5) sell rows with the nightly decision
s = s.replace(
    """    sell_rows = []
    for p in positions:
        sell_rows.append({**p, "decision": decide_label(p["pnl_pct"], p["total_pct"], CFG)})""",
    "    sell_rows = Wdata.sell_rows()", 1)

# 6) chart from WarRobot's realised P/L
s = s.replace("chart=svg_chart(per_symbol_series()), chart_version=\"v8\"",
              "chart=svg_chart(Wdata.chart_points()), chart_version=\"v9-warrobot\"", 1)

# 7) target/stop from WarRobot's config when present
s = s.replace('    target = float(CFG.get("sell.target_pct", 12.0))',
              '''    try:
        import json as _j
        _wr = _j.loads((Path(r"C:\\Users\\Administrator\\Desktop\\DeepSeek DeskTop\\WarRobot\\config.json")).read_text(encoding="utf-8"))
        target = float(_wr["sell"].get("target_pct", 25.0))
        stop = float(_wr["sell"].get("stop_pct", 3.0))
    except Exception:
        target = float(CFG.get("sell.target_pct", 12.0))''', 1)
s = s.replace('    stop = float(CFG.get("sell.stop_pct", 3.0))',
              '''    try:
        stop = stop
    except Exception:
        stop = float(CFG.get("sell.stop_pct", 3.0))''', 1)

app.write_text(s, encoding="utf-8", errors="surrogateescape")
print("app.py pointed at WarRobot data")
