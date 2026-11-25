"""Cancel the orders that never filled and rotate into the next candidate.

Called once per session at `cancel_unfilled_at` (10:00 by default).

  1) read the open buy orders from the panel and keep the ones with 0 filled
  2) cancel them (the fourth cell of the grid row is the delete action)
  3) buy the next reserve symbols with the freed cash, one order each

The reserve list is written by queue_scan.py (state/reserve.csv): the locked
symbols that did not make the top three.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pandas as pd

BASE = Path(r"C:\Users\Administrator\Desktop\DeepSeek DeskTop\WarRobot")
sys.path.insert(0, str(BASE / "src"))
sys.path.insert(0, str(BASE))

import distbot as bot  # noqa: E402

RESERVE = BASE / "state" / "reserve.csv"


def _cdp():
    sys.path.insert(0, r"C:\Users\Administrator\Desktop\DeepSeek DeskTop\TeseRobot\src")
    from tsebot.cdp import CDP
    return CDP()


def unfilled_orders() -> list[dict]:
    """Rows of the open-orders grid whose filled quantity is zero."""
    c = _cdp()
    try:
        raw = c.eval("""(function(){
        var out=[];
        document.querySelectorAll('ul.g-row').forEach(function(r){
          var t=(r.innerText||'').replace(/\\s+/g,' ');
          if(t.indexOf('خرید')<0) return;
          var li=r.querySelectorAll('li');
          var sym='', qty='', done='';
          for(var i=0;i<li.length;i++){
            var v=(li[i].innerText||'').trim().replace(/,/g,'');
            if(!sym && v && v.length<=12 && /^[\\u0600-\\u06FF0-9 ]+$/.test(v)) sym=v;
            if(/^[0-9]+$/.test(v) && !qty) qty=v;
          }
          out.push(t.slice(0,160));
        });
        return JSON.stringify(out.slice(0,12));})()""") or "[]"
        rows = json.loads(raw)
        return [{"raw": r} for r in rows]
    finally:
        try:
            c.close()
        except Exception:
            pass


def cancel_row(symbol: str, qty: str) -> bool:
    c = _cdp()
    try:
        pos = c.eval("""(function(sym, qty){
        var target=null;
        document.querySelectorAll('ul.g-row').forEach(function(r){
          if(target) return;
          var t=(r.innerText||'').replace(/\\s+/g,' ');
          if(t.indexOf(sym)>=0 && t.indexOf(qty)>=0 && t.indexOf('خرید')>=0) target=r;});
        if(!target) return '';
        var li=target.querySelectorAll('li')[4];
        if(!li) return '';
        var b=li.getBoundingClientRect();
        return JSON.stringify({x:b.x+b.width/2,y:b.y+b.height/2});})("""
                         + json.dumps(symbol, ensure_ascii=False) + ","
                         + json.dumps(str(qty)) + """)""") or ""
        if not pos:
            return False
        p = json.loads(pos)
        for t in ("mousePressed", "mouseReleased"):
            c.call("Input.dispatchMouseEvent", {"type": t, "x": p["x"], "y": p["y"],
                                                "button": "left", "clickCount": 1})
        time.sleep(2.5)
        return True
    finally:
        try:
            c.close()
        except Exception:
            pass


def rotate(dry: bool = False) -> dict:
    """Cancel the unfilled buys and spend the freed cash on the reserve list."""
    out = {"cancelled": [], "placed": [], "reserve": []}
    rows = unfilled_orders()
    # open orders still pending = candidates that never filled
    open_syms = bot.symbols_in_open_orders()
    held = bot.bought_today()
    for sym in open_syms:
        if dry:
            out["cancelled"].append({"symbol": sym, "dry": True})
            continue
        ok = cancel_row(sym, "")   # qty empty -> match only the symbol
        if ok:
            out["cancelled"].append({"symbol": sym})
            time.sleep(1.5)
    if dry:
        out["dry"] = True
        return out
    if not out["cancelled"]:
        return out

    if not RESERVE.exists():
        return out
    rv = pd.read_csv(RESERVE)
    power = bot.cdp_power()
    for _, r in rv.iterrows():
        sym = str(r["symbol"])
        if sym in held or len(out["placed"]) >= len(out["cancelled"]):
            continue
        price = float(r["next_ceiling"])
        qty = int((power / max(len(out["cancelled"]), 1)) / (price * 1.015))
        if qty <= 0:
            continue
        probe = bot.submit_via_cdp("buy", sym, 0, None)
        live = float(probe.get("price") or 0)
        if live and price and abs(live / price - 1) > 0.20:
            out["placed"].append({"symbol": sym, "skipped": "price_mismatch", "live": live, "file": price})
            continue
        res = bot.submit_via_cdp("buy", sym, qty, None)
        out["placed"].append({"symbol": sym, "qty": qty, "res": res})
        time.sleep(2)
    return out


if __name__ == "__main__":
    print(json.dumps(rotate(dry="--dry" in sys.argv), ensure_ascii=False, default=str)[:800])


STATE = BASE / "state" / "rotate_state.json"


def _load_state() -> dict:
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except Exception:
        return {"session": "", "used": []}


def _save_state(st: dict) -> None:
    STATE.write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")


def rotate_loop() -> dict:
    """Keep cancelling the unfilled order and moving to the next candidate.

    Runs once per call; the daemon calls it every ~10 minutes between 09:05 and
    12:25. Every symbol from the reserve list is used at most once per session.
    """
    st = _load_state()
    today = bot.now_tehran().strftime("%Y-%m-%d")
    if st.get("session") != today:
        st = {"session": today, "used": []}
        _save_state(st)

    out = {"session": today, "cancelled": [], "placed": [], "note": ""}
    open_syms = bot.symbols_in_open_orders()
    pending = [s for s in open_syms if s not in st["used"]]
    if not pending:
        out["note"] = "no pending order"
        return out

    for sym in pending:
        if cancel_row(sym, ""):
            out["cancelled"].append(sym)
            st["used"].append(sym)
            _save_state(st)
            time.sleep(1.5)

    if not out["cancelled"]:
        out["note"] = "could not cancel"
        return out

    if not RESERVE.exists():
        out["note"] = "no reserve list"
        return out
    rv = pd.read_csv(RESERVE)
    power = bot.cdp_power()
    for _, r in rv.iterrows():
        sym = str(r["symbol"])
        if sym in st["used"]:
            continue
        price = float(r["next_ceiling"])
        probe = bot.submit_via_cdp("buy", sym, 0, None)
        live = float(probe.get("price") or 0)
        if live and price and abs(live / price - 1) > 0.20:
            st["used"].append(sym)
            _save_state(st)
            out["placed"].append({"symbol": sym, "skipped": "price_mismatch", "live": live})
            continue
        qty = int((power / max(len(out["cancelled"]), 1)) / ((live or price) * 1.015))
        if qty <= 0:
            continue
        res = bot.submit_via_cdp("buy", sym, qty, None)
        st["used"].append(sym)
        _save_state(st)
        out["placed"].append({"symbol": sym, "qty": qty, "ok": bool(res.get("ok")),
                              "msg": str(res.get("message") or "")[:60]})
        break
    return out
