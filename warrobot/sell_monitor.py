"""Nightly portfolio review + automatic exits through the Playwright path.

Runs right after the nightly scan:
  1. rebuilds state/sell_candidates.csv — every open position with its TP/SL,
     the latest price, the P/L and the decision the bot would take
  2. places a sell order (Playwright, the working path) for every position whose
     P/L has reached its take-profit or stop-loss
  3. records everything in state/orders_log.jsonl and state/sell_review.json

The web app reads these files, so refreshing them at night is what keeps the
candidate / sell / P&L tabs current.
"""
from __future__ import annotations

import sys as _sys
try:
    _sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import json
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
PW = BASE / "pw"
CAND = BASE / "state" / "sell_candidates.csv"
REVIEW = BASE / "state" / "sell_review.json"
LOG = BASE / "state" / "orders_log.jsonl"


def _ledger_rows() -> list[dict]:
    fp = BASE / "state" / "ledger.jsonl"
    if not fp.exists():
        return []
    out = []
    for line in fp.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except Exception:
            pass
    return out


def positions() -> list[dict]:
    buys, sells = {}, {}
    for r in _ledger_rows():
        sym = r.get("symbol")
        if not sym:
            continue
        q = float(r.get("qty") or 0)
        p = float(r.get("price") or 0)
        if r.get("side") == "buy":
            b = buys.setdefault(sym, {"qty": 0.0, "cost": 0.0})
            b["qty"] += q
            b["cost"] += q * p
        elif r.get("side") == "sell":
            s = sells.setdefault(sym, 0.0)
            sells[sym] = s + q
    out = []
    for sym, b in buys.items():
        qty = b["qty"] - sells.get(sym, 0.0)
        if qty <= 0:
            continue
        out.append({"symbol": sym, "qty": int(qty), "avg_cost": b["cost"] / b["qty"] if b["qty"] else 0.0})
    return out


def _targets() -> dict:
    fp = BASE / "state" / "targets.json"
    try:
        return json.loads(fp.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _last_price(symbol: str) -> float:
    p = BASE.parent / "TeseRobot" / "data" / "daily" / f"{symbol}_daily.csv"
    try:
        d = pd.read_csv(p, dtype={"date": str})
        return float(d.sort_values("date")["Close"].iloc[-1])
    except Exception:
        return 0.0


def _sell(symbol: str, isin: str, qty: int, price: float) -> dict:
    try:
        p = subprocess.run([NODE, str(PW / "flow.js"), symbol, isin, str(qty), str(price), "0", "sell"],
                           cwd=str(PW), capture_output=True, text=True, timeout=900,
                           encoding="utf-8", errors="replace")
        for line in reversed((p.stdout or "").splitlines()):
            if line.strip().startswith("{"):
                return json.loads(line)
    except Exception as exc:
        return {"message": f"error:{str(exc)[:60]}"}
    return {}


def run(place_orders: bool = True) -> dict:
    tgt = _targets()
    rows, acts = [], []
    try:  # Hark F3: only legacy V3 positions may be judged here; fund + افران/خساپا never
        import fund_ledger as _fl
        _fund_mode = _fl.raw_fund_enabled()
        _legacy = _fl.legacy_v3() if _fund_mode else None
    except Exception:
        _fund_mode, _legacy = True, set()
    for pos in positions():
        sym = pos["symbol"]
        if sym in ("افران", "خساپا"):
            continue
        if _fund_mode and str(sym).replace("ي", "ی").replace("ك", "ک") not in (_legacy or set()):
            continue
        t = tgt.get(sym, {})
        tp = float(t.get("tp") or 22.0)
        sl = float(t.get("sl") or 4.0)
        px = _last_price(sym)
        pnl = ((px / pos["avg_cost"]) - 1) * 100 if pos["avg_cost"] > 0 and px else 0.0
        decision = "hold"
        if pnl >= tp:
            decision = "sell_tp"
        elif pnl <= -sl:
            decision = "sell_sl"
        rows.append({"symbol": sym, "qty": pos["qty"], "avg_cost": round(pos["avg_cost"], 1),
                     "last": px, "pnl_pct": round(pnl, 2), "tp": tp, "sl": sl, "decision": decision})
        if decision != "hold" and place_orders:
            try:
                import tsetmc as api
                inst = api.resolve(sym)
                isin = str((inst or {}).get("isin") or "")
            except Exception:
                isin = ""
            res = _sell(sym, isin, pos["qty"], px) if isin else {"message": "no_isin"}
            rec = {"symbol": sym, "qty": pos["qty"], "price": px, "side": "sell",
                   "message": res.get("message"), "reason": decision}
            rec["submitted"] = bool(any(k in str(rec["message"]) for k in ("موفق", "ثبت")))
            acts.append(rec)
            with LOG.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")

    pd.DataFrame(rows).to_csv(CAND, index=False, encoding="utf-8-sig") if rows else None
    out = {"at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "positions": rows, "actions": acts}
    REVIEW.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    return out


if __name__ == "__main__":
    print(json.dumps(run(place_orders="--dry" not in sys.argv), ensure_ascii=False, indent=1)[:1200])
