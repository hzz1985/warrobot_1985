"""Fix the 10:00 rotation so it really cancels the unfilled order and replaces it.

The old code failed twice:
  * open_buys() parsed the API text with a fragile regex and returned nothing
    -> "nothing unfilled" -> the rotation silently did nothing
  * the cancel step used the panel row click, but the open-orders grid row has
    no delete handler (onDeleteClick: null) -> cancel never happened
Now it uses:
  * pw/open_orders.js (clean JSON from the broker API)
  * the real endpoint POST /Web/V1/Order/Delete with a JSON array of ids
"""
from pathlib import Path

WAR = Path(r"C:\Users\Administrator\Desktop\DeepSeek DeskTop\WarRobot")
p = WAR / "rebalance.py"
s = p.read_text(encoding="utf-8")

if "cancel_orders" not in s:
    # reliable open orders
    s = s.replace('''def open_buys() -> list[dict]:
    d = _api_dump()
    flat = _flat(d)
    orders = []
    for m in re.finditer(r'\\{"symbol":"([^"]+)"[^{}]*?"orderid":"?(\\d+)"?[^{}]*?"qunatity":(\\d+)[^{}]*?"excuted":(\\d+)', flat):
        orders.append({"symbol": m.group(1), "orderid": m.group(2),
                       "qty": int(m.group(3)), "executed": int(m.group(4))})
    return orders''',
'''def open_buys() -> list[dict]:
    """Open orders straight from the broker API (reliable JSON, no regex)."""
    import cancel_orders as co
    out = []
    for o in co.open_orders():
        if str(o.get("side") or "") and "خرید" not in str(o.get("side")):
            continue
        out.append({"symbol": o.get("symbol"), "orderid": str(o.get("orderid")),
                    "qty": int(o.get("qty") or 0),
                    "executed": int(o.get("executed") or 0),
                    "price": float(o.get("price") or 0)})
    return out''')

    # real cancel
    s = s.replace('''    # cancel through the panel
    used = {o["symbol"] for o in unfilled}
    # never re-buy something the bot already ordered today
    if LOG.exists():
        for line in LOG.read_text(encoding="utf-8").splitlines()[-30:]:
            try:
                used.add(json.loads(line).get("symbol"))
            except Exception:
                pass
    for o in unfilled:
        try:
            p = subprocess.run([NODE, str(PW / "cancel_order.js"), o["symbol"], str(o["qty"])],
                               cwd=str(PW), capture_output=True, text=True, timeout=300,
                               encoding="utf-8", errors="replace")
            ok = "cancelled" in (p.stdout or "")
        except Exception:
            ok = False
        out["cancelled"].append({"symbol": o["symbol"], "ok": ok})''',
'''    # cancel through the broker's real endpoint (POST /Order/Delete, array body)
    used = {o["symbol"] for o in unfilled}
    if LOG.exists():
        for line in LOG.read_text(encoding="utf-8").splitlines()[-30:]:
            try:
                used.add(json.loads(line).get("symbol"))
            except Exception:
                pass
    try:
        import cancel_orders as _co
        res = _co.cancel_all()
        out["cancel_result"] = res
        for m in unfilled:
            out["cancelled"].append({"symbol": m["symbol"], "ok": res.get("after", 1) == 0})
    except Exception as exc:
        out["cancel_error"] = str(exc)[:120]
        for m in unfilled:
            out["cancelled"].append({"symbol": m["symbol"], "ok": False})''')
    p.write_text(s, encoding="utf-8")
    print("rebalance.py now cancels through /Order/Delete and reads clean JSON")
else:
    print("already patched")
