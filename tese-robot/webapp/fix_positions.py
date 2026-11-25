"""Fill the broker portfolio's empty cost/price fields for the dashboard.

The broker returns avg=0 and last=0 for some positions (e.g. آردینه, افران),
which made the position table show zeros. Fall back to:
    avg   <- the bot's ledger average
    price <- the last daily close (TeseRobot data/daily)
"""
from pathlib import Path

p = Path(r"C:\Users\Administrator\Desktop\DeepSeek DeskTop\TeseRobot\webapp\warrobot_data.py")
s = p.read_text(encoding="utf-8")

if "_ledger_avg" not in s:
    s = s.replace('''def positions() -> list[dict]:''',
'''def _ledger_avg() -> dict:
    """average cost per symbol from the bot's ledger (fallback for broker avg=0)."""
    out: dict[str, float] = {}
    for r in _ledger():
        sym = r.get("symbol")
        if not sym or r.get("side") != "buy":
            continue
        q, price = float(r.get("qty") or 0), float(r.get("price") or 0)
        b = out.get("__q_" + sym, 0.0)
        out["__q_" + sym] = b + q
        out["__c_" + sym] = out.get("__c_" + sym, 0.0) + q * price
    avg = {}
    for k in list(out):
        if k.startswith("__q_"):
            sym = k[4:]
            q = out[k]
            if q > 0:
                avg[sym] = out.get("__c_" + sym, 0.0) / q
    return avg


def _daily_close(symbol: str) -> float:
    fp = WAR.parent / "TeseRobot" / "data" / "daily" / f"{symbol}_daily.csv"
    try:
        import pandas as pd
        df = pd.read_csv(fp)
        return float(df["Close"].iloc[-1])
    except Exception:
        return 0.0


def positions() -> list[dict]:''')
    s = s.replace('''    broker = _broker_positions()
    if broker:
        out = []
        for p in broker:
            avg = p["avg"] or 0.0
            px = p["price"] or prices.get(p["symbol"]) or avg''',
'''    broker = _broker_positions()
    if broker:
        out = []
        avgs = _ledger_avg()
        for p in broker:
            avg = p["avg"] or avgs.get(p["symbol"]) or 0.0
            px = (p["price"] or prices.get(p["symbol"])
                  or _daily_close(p["symbol"]) or avg)''')
    p.write_text(s, encoding="utf-8")
    print("positions fallback added")
else:
    print("already patched")
