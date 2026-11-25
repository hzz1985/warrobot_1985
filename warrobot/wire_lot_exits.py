"""Add live selling to lot_exits (lot-based TP/SL) and wire it into the watch loop.

When a lot's own +25% / -3% is reached, the bot sells exactly that lot's quantity.
Strategy parameters are unchanged; only the accounting is per purchase, which is
what the operator expects (the first آواپارس lot touched +25% at 8,645 while the
blended average showed only ~+8% after the top-up).
"""
from pathlib import Path

P = Path(r"C:\Users\Administrator\Desktop\DeepSeek DeskTop\WarRobot\lot_exits.py")
s = P.read_text(encoding="utf-8")

if "def sell_due(" not in s:
    s = s.replace('''if __name__ == "__main__":''',
'''def resolve_isin(symbol: str) -> str:
    try:
        import tsetmc as api
        return str((api.resolve(symbol) or {}).get("isin") or "")
    except Exception:
        return ""


def sell_due(out: dict) -> list[dict]:
    """Place a sell order for every lot that reached its own TP/SL."""
    placed = []
    for d in out.get("due") or []:
        sym = d["symbol"]
        qty = int(d.get("qty") or 0)
        price = int(float(prices_cache.get(sym) or 0))
        if qty <= 0 or price <= 0:
            continue
        isin = resolve_isin(sym)
        try:
            p = subprocess.run([NODE, str(PW / "flow.js"), sym, isin,
                                str(qty), str(price), "0", "sell"],
                               cwd=str(PW), capture_output=True, text=True,
                               timeout=1200, encoding="utf-8", errors="replace")
            data = {}
            for line in reversed((p.stdout or "").splitlines()):
                if line.strip().startswith("{"):
                    data = json.loads(line)
                    break
        except Exception as exc:
            data = {"message": f"error:{str(exc)[:80]}"}
        rec = {"at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
               "symbol": sym, "qty": qty, "price": price,
               "reason": f"lot_{d.get('reason')}", "pct": d.get("pct"),
               "message": data.get("message"),
               "submitted": bool(data.get("submitted"))}
        placed.append(rec)
        try:
            (BASE / "state" / "exits_log.jsonl").open("a", encoding="utf-8").write(
                json.dumps(rec, ensure_ascii=False) + "\\n")
        except Exception:
            pass
        if rec["submitted"]:
            try:
                import trade_journal as tj
                tj.record_sell(sym, qty, price, reason=rec["reason"], at=rec["at"])
            except Exception:
                pass
    out["placed"] = placed
    return placed


prices_cache: dict = {}


if __name__ == "__main__":''')

    s = s.replace('''    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")''',
'''    if not dry:
        out["placed"] = sell_due(out)
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")''')

    s = s.replace('''    prices = {str(p["symbol"]): float(p.get("price") or 0)
              for p in pnl.get("positions", [])}''',
'''    global prices_cache
    prices = {str(p["symbol"]): float(p.get("price") or 0)
              for p in pnl.get("positions", [])}
    prices_cache = dict(prices)''')
    P.write_text(s, encoding="utf-8")
    print("lot_exits: live selling added")

# intraday_exit: run the lot-based pass before the blended logic
IE = Path(r"C:\Users\Administrator\Desktop\DeepSeek DeskTop\WarRobot\intraday_exit.py")
t = IE.read_text(encoding="utf-8")
if "lot_exits" not in t:
    t = t.replace('''    import pnl
    data = pnl.build()''',
'''    # lot-based TP/SL first: each purchase is judged on its own cost
    try:
        import lot_exits
        lot_exits.main(dry=False)
    except Exception as exc:
        print(f"lot_exits error: {str(exc)[:90]}", flush=True)

    import pnl
    data = pnl.build()''')
    IE.write_text(t, encoding="utf-8")
    print("intraday_exit: runs lot_exits first")
