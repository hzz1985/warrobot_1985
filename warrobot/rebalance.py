"""One-third-of-total-assets sizing + 10:00 rotation of unfilled orders.

Total assets = cash (RealBalance) + market value of the current portfolio, both
from the broker API. Every new position gets exactly one third of that number.

rotate() = the 10:00 job:
  1. read the open buy orders
  2. keep the ones still not filled (excuted < qunatity)
  3. cancel them through the panel
  4. place an order for the next candidate from state/candidates.csv (rows 4..6)
     and then from state/reserve.csv
"""
from __future__ import annotations

import sys as _sys
try:
    _sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import json
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

BASE = Path(r"C:\Users\Administrator\Desktop\DeepSeek DeskTop\WarRobot")
sys.path.insert(0, str(BASE / "src"))
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE.parent / "Tse2" / "orderflow"))

import pandas as pd  # noqa: E402

NODE = r"C:\Program Files\nodejs\node.exe"


def _clear_lock() -> None:
    prof = BASE / "state" / "pw-profile"
    for n in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
        try:
            (prof / n).unlink(missing_ok=True)
        except Exception:
            pass
PW = BASE / "pw"
CAND = BASE / "state" / "candidates.csv"
RESERVE = BASE / "state" / "reserve.csv"
LOG = BASE / "state" / "orders_log.jsonl"


def _api_dump() -> dict:
    """One Playwright round trip: login + balance + portfolio + open orders."""
    try:
        p = subprocess.run([NODE, str(PW / "check2.js")], cwd=str(PW), capture_output=True,
                           text=True, timeout=600, encoding="utf-8", errors="replace")
        for line in reversed((p.stdout or "").splitlines()):
            if line.strip().startswith("{"):
                return json.loads(line)
    except Exception:
        pass
    return {}


def _flat(d: dict) -> str:
    return json.dumps(d, ensure_ascii=False).replace("\\", "")


def _last_close(symbol: str) -> float:
    """Freshest price: TSETMC close first, then the local daily file."""
    try:
        import tsetmc as api
        inst = api.resolve(symbol)
        if inst:
            rows = (api.closing_daily(inst["ins_code"], 5) or {}).get("closingPriceDaily") or []
            if rows:
                # TSETMC returns newest-first; sort by date to be certain
                rows = sorted(rows, key=lambda r: str(r.get("dEven") or ""))
                px = float(rows[-1].get("pClosing") or 0)
                if px > 0:
                    return px
    except Exception:
        pass
    fp = BASE.parent / "TeseRobot" / "data" / "daily" / f"{symbol}_daily.csv"
    try:
        import pandas as pd
        df = pd.read_csv(fp)
        return float(df["Close"].iloc[-1])
    except Exception:
        return 0.0


def total_assets() -> float:
    """Real total assets = cash + market value of the portfolio."""
    d = _api_dump()
    flat = _flat(d)
    cash = 0.0
    m = re.search(r'"RealBalance":([\d.]+)', flat)
    if m:
        cash = float(m.group(1))

    holdings = 0.0
    try:
        import portfolio_sync as ps
        excluded = {"خساپا"}
        for pos in ps.broker_positions():
            if str(pos.get("symbol")) in excluded:
                continue          # not part of the invested capital
            qty = float(pos.get("qty") or 0)
            if qty <= 0:
                continue
            px = float(pos.get("avg") or 0)          # broker average, fallback
            close = _last_close(str(pos.get("symbol")))
            if close > 0:
                px = close                          # market value wins
            elif px <= 0:
                px = 0.0
            holdings += qty * px
    except Exception:
        # fallback: the old raw-text estimate
        for m2 in re.finditer(r'"RunTimeShareCount":(\d+)[^{}]*?"BuyPriceAvg":([\d.]+)', flat):
            holdings += int(m2.group(1)) * float(m2.group(2))
    return cash + holdings


def open_buys() -> list[dict]:
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
    return out


def _next_candidates(used: set, need: int) -> list[dict]:
    out = []
    for path in (CAND, RESERVE):
        if not path.exists():
            continue
        for _, r in pd.read_csv(path).iterrows():
            sym = str(r["symbol"])
            if sym in used or len(out) >= need:
                continue
            out.append({"symbol": sym, "close": float(r.get("close") or 0)})
    return out


def run(dry: bool = False) -> dict:
    out = {"at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "cancelled": [], "placed": []}
    orders = open_buys()
    unfilled = [o for o in orders if o["executed"] < o["qty"]]
    out["open"] = orders
    out["unfilled"] = unfilled
    if not unfilled:
        out["note"] = "nothing unfilled"
        return out

    assets = total_assets()
    third = assets / 3.0
    # free_cash_cap: never size an order above the money we actually have
    try:
        import json as _json
        from pathlib import Path as _P
        _snap = _json.loads((_P(__file__).resolve().parent / "state" / "prices.json"
                             ).read_text(encoding="utf-8"))
        free_cash = float((_snap.get("balance") or {}).get("account_balance") or 0)
    except Exception:
        free_cash = 0.0
    if free_cash > 0:
        third = min(third, free_cash * 0.95)
    out["free_cash"] = round(free_cash, 0)
    out["total_assets"] = round(assets, 0)
    out["per_symbol_budget"] = round(third, 0)

    if dry:
        out["would_cancel"] = [o["symbol"] for o in unfilled]
        out["would_buy"] = [c["symbol"] for c in _next_candidates(
            {o["symbol"] for o in unfilled}, len(unfilled) + 4)]
        return out

    # cancel through the broker's real endpoint (POST /Order/Delete, array body)
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
            out["cancelled"].append({"symbol": m["symbol"], "ok": False})

    # order the next candidates with the same 1/3 sizing
    placed_ok = False
    for c in _next_candidates(used, len(unfilled) + 4):
        if placed_ok:
            break
        px = c["close"]
        qty = int(third / (px * 1.015)) if px > 0 else 0
        rec = {"symbol": c["symbol"], "qty": qty, "price": px, "budget": round(third, 0)}
        if qty <= 0:
            out["placed"].append({**rec, "ok": False, "reason": "bad_qty"})
            continue
        try:
            import tsetmc as api
            inst = api.resolve(c["symbol"])
            isin = str((inst or {}).get("isin") or "")
        except Exception:
            isin = ""
        if not isin:
            out["placed"].append({**rec, "ok": False, "reason": "no_isin"})
            continue
        try:
            p = subprocess.run([NODE, str(PW / "flow.js"), c["symbol"], isin, str(qty), str(px)],
                               cwd=str(PW), capture_output=True, text=True, timeout=900,
                               encoding="utf-8", errors="replace")
            data = {}
            for line in reversed((p.stdout or "").splitlines()):
                if line.strip().startswith("{"):
                    data = json.loads(line)
                    break
        except Exception as exc:
            data = {"message": f"error:{str(exc)[:60]}"}
        rec["message"] = data.get("message")
        rec["ok"] = bool(re.search(r"موفق|ثبت", str(rec["message"] or "")))
        out["placed"].append(rec)
        with LOG.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return out


if __name__ == "__main__":
    print(json.dumps(run(dry="--dry" in sys.argv), ensure_ascii=False, indent=1)[:1200])
