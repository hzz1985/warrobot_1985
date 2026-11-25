"""Apply today's four requests.

1) the dashboard shows the BROKER portfolio (source of truth), not the ledger
2) portfolio_sync runs every night right after the scan
3) the 08:45 order fires at exactly 08:45:01 (the scheduler waits for the second)
4) realised profits are moved into the fixed-income stock افران automatically,
   and افران is never sold by the bot (the operator decides that manually)
"""
from pathlib import Path

WAR = Path(r"C:\Users\Administrator\Desktop\DeepSeek DeskTop\WarRobot")
WEB = WAR.parent / "TeseRobot" / "webapp"

# ---------------------------------------------------------------- 1) dashboard
adapter = WEB / "warrobot_data.py"
s = adapter.read_text(encoding="utf-8")
if "broker_portfolio.json" not in s:
    s = s.replace('''def positions() -> list[dict]:''',
'''def _broker_positions() -> list[dict]:
    """The broker's real portfolio — the authoritative source for the dashboard."""
    fp = STATE / "broker_portfolio.json"
    if not fp.exists():
        return []
    try:
        d = json.loads(fp.read_text(encoding="utf-8"))
    except Exception:
        return []
    out = []
    for p in d.get("broker", []):
        try:
            qty = int(p.get("qty") or 0)
        except Exception:
            continue
        if qty <= 0:
            continue
        out.append({"symbol": p["symbol"], "qty": qty,
                    "avg": round(float(p.get("avg") or 0), 1),
                    "price": round(float(p.get("last") or 0), 1)})
    return out


def positions() -> list[dict]:''')
    s = s.replace('''    prices = _price_map()
    out = []''',
'''    prices = _price_map()
    broker = _broker_positions()
    if broker:
        out = []
        for p in broker:
            avg = p["avg"] or 0.0
            px = p["price"] or prices.get(p["symbol"]) or avg
            value = p["qty"] * px
            invested = p["qty"] * avg
            out.append({
                "symbol": p["symbol"], "qty": p["qty"], "avg": avg, "price": px,
                "value": round(value, 0), "invested": round(invested, 0),
                "pnl_pct": round((px / avg - 1) * 100, 2) if avg else 0.0,
                "total_pct": round((px / avg - 1) * 100, 2) if avg else 0.0,
                "total_pnl": round(value - invested, 0),
            })
        return out
    out = []''')
    adapter.write_text(s, encoding="utf-8")
    print("dashboard now prefers the broker portfolio")
else:
    print("dashboard already wired")

# ------------------------------------------------- 2) nightly sync in scheduler
sch = WAR / "scheduler.py"
sc = sch.read_text(encoding="utf-8")
if "portfolio_sync.py" not in sc:
    sc = sc.replace('''    ("21:25", "sellrev", ["sell_monitor.py"]),''',
'''    ("21:20", "portsync", ["portfolio_sync.py"]),
    ("21:25", "sellrev", ["sell_monitor.py"]),''')
    sch.write_text(sc, encoding="utf-8")
    print("nightly portfolio sync scheduled at 21:20")
else:
    print("portfolio sync already scheduled")

# ------------------------------------------- 3) exact 08:45:01 in the scheduler
sc = sch.read_text(encoding="utf-8")
if "wait_for_084501" not in sc:
    sc = sc.replace('''def _run(job: str, script: str) -> None:''',
'''def wait_for_084501() -> None:
    """Block until 08:45:01 Tehran so the order leaves at the exact second."""
    import time as _t
    for _ in range(70):
        now = bot.now_tehran()
        if now.strftime("%H:%M") != "08:45":
            return
        if now.second >= 1:
            return
        _t.sleep(0.5)


def _run(job: str, script: str) -> None:''')
    sc = sc.replace('''            bot.log(f"[sched] firing {job} at {at} (now {hm})")
            _run(job, argv[0])''',
'''            bot.log(f"[sched] firing {job} at {at} (now {hm})")
            if job == "order":
                wait_for_084501()
            _run(job, argv[0])''')
    sch.write_text(sc, encoding="utf-8")
    print("scheduler now waits for 08:45:01 before ordering")
else:
    print("exact-second wait already present")

# ----------------------------------------- 4) profit transfer to افران + rules
exit_py = WAR / "intraday_exit.py"
e = exit_py.read_text(encoding="utf-8")
if "AFRAN" not in e:
    e = e.replace('''BASE = Path(r"C:\\Users\\Administrator\\Desktop\\DeepSeek DeskTop\\WarRobot")''',
'''BASE = Path(r"C:\\Users\\Administrator\\Desktop\\DeepSeek DeskTop\\WarRobot")
# fixed-income stock that receives every realised profit; never auto-sold
AFRAN_SYMBOL = "افران"
AFRAN_ISIN = "IRT1AFRN0001"
MANUAL_ONLY = {AFRAN_SYMBOL}''')
    e = e.replace('''def main(place: bool = True, notify: bool = True) -> int:''',
'''def transfer_profit_to_afran(profit_rial: float) -> dict:
    """Buy افران with the realised profit (never sell it)."""
    if profit_rial < 5_000_000:
        return {"skipped": "profit below the minimum order value"}
    try:
        import tsetmc as api
        inst = api.resolve(AFRAN_SYMBOL)
        isin = str((inst or {}).get("isin") or AFRAN_ISIN)
        rows = (api.closing_daily(inst["ins_code"], 5) or {}).get("closingPriceDaily") or []
        price = float(rows[-1].get("pClosing") or 0) if rows else 0
    except Exception:
        isin, price = AFRAN_ISIN, 0.0
    if price <= 0:
        return {"skipped": "no price for افران"}
    qty = int(profit_rial / (price * 1.015))
    if qty <= 0:
        return {"skipped": "qty=0"}
    try:
        p = subprocess.run([NODE, str(PW / "flow.js"), AFRAN_SYMBOL, isin,
                            str(qty), str(price)],
                           cwd=str(PW), capture_output=True, text=True, timeout=900,
                           encoding="utf-8", errors="replace")
        for line in reversed((p.stdout or "").splitlines()):
            if line.strip().startswith("{"):
                d = json.loads(line)
                d["profit_rial"] = round(profit_rial, 0)
                d["afran_qty"] = qty
                return d
    except Exception as exc:
        return {"error": str(exc)[:80]}
    return {}


def main(place: bool = True, notify: bool = True) -> int:''')
    e = e.replace('''    for pos in positions():
        sym = pos["symbol"]''',
'''    for pos in positions():
        sym = pos["symbol"]
        if sym in MANUAL_ONLY:
            continue          # افران is never sold by the bot''')
    # after a successful sell, move the profit into افران
    e = e.replace('''        out["exits"].append(rec)''',
'''        if rec["submitted"]:
            profit = (px - pos["avg"]) * pos["qty"] * (1 - FEE) if pos["avg"] else 0.0
            rec["afran_transfer"] = transfer_profit_to_afran(profit)
        out["exits"].append(rec)''')
    if "FEE" not in e.split("\n")[30]:
        e = e.replace("NODE = r\"C:\\Program Files\\nodejs\\node.exe\"",
                      "NODE = r\"C:\\Program Files\\nodejs\\node.exe\"\nFEE = 0.005")
    exit_py.write_text(e, encoding="utf-8")
    print("profit transfer to افران added; افران excluded from auto-sell")
else:
    print("afran rule already present")
