"""One source of truth for positions, average cost and P/L.

Formula (the one the operator expects):
    pnl_pct = (price / avg_cost - 1) * 100
    value   = qty * price
    to_tp   = take_profit_pct - pnl_pct        # percentage points left to target
    to_sl   = pnl_pct + stop_loss_pct          # percentage points above the stop

Where the numbers come from (priority order):
    qty      : broker portfolio (panel DOM, authoritative)
    price    : broker panel last traded price (DOM)
    avg_cost : state/manual_costs.json  >  broker average (>0)  >  bot ledger average
A manual value lets the operator pin the true average cost that the broker API
does not expose (BuyPriceAvg is 0 for some positions); the source is reported so
the dashboard never presents a guess as a fact.

Outputs: state/pnl.json
"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

BASE = Path(r"C:\Users\Administrator\Desktop\DeepSeek DeskTop\WarRobot")
sys.path.insert(0, str(BASE / "src"))
sys.path.insert(0, str(BASE))

NODE = r"C:\Program Files\nodejs\node.exe"
PW = BASE / "pw"
OUT = BASE / "state" / "pnl.json"
EXCLUDED_SYMBOLS = {"خساپا"}   # out of the portfolio list, decisions and maths
MANUAL = BASE / "state" / "manual_costs.json"


def _cfg() -> dict:
    try:
        return json.loads((BASE / "config.json").read_text(encoding="utf-8"))["sell"]
    except Exception:
        return {"target_pct": 25.0, "stop_pct": 3.0}


def _manual() -> dict:
    try:
        return {k: float(v) for k, v in json.loads(MANUAL.read_text(encoding="utf-8")).items()}
    except Exception:
        return {}


def _ledger_avg() -> dict:
    import ledger_sync
    rows = ledger_sync._ledger_rows()
    pos = ledger_sync._ledger_positions(rows)
    return {k: (v["cost"] / v["qty"]) for k, v in pos.items() if v["qty"] > 0}


def _broker_avg() -> dict:
    try:
        d = json.loads((BASE / "state" / "broker_portfolio.json").read_text(encoding="utf-8"))
        return {p["symbol"]: float(p.get("avg") or 0) for p in d.get("broker", [])}
    except Exception:
        return {}


def panel_positions() -> list[dict]:
    """Broker's advanced portfolio: real average cost + exact P/L (authoritative)."""
    import pw_lock
    with pw_lock.guard("pnl_panel") as got:
        if not got:
            return []
        try:
            p = subprocess.run([NODE, str(PW / "advanced_portfolio_values.js")],
                               cwd=str(PW), capture_output=True, text=True, timeout=600,
                               encoding="utf-8", errors="replace")
            for line in reversed((p.stdout or "").splitlines()):
                if line.strip().startswith("{"):
                    return json.loads(line).get("positions") or []
        except Exception:
            pass
    return []


HISTORY = BASE / "state" / "equity_history.jsonl"


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


def _tehran_today() -> str:
    try:
        import market_cal
        return market_cal.now_tehran().strftime("%Y-%m-%d")
    except Exception:
        return datetime.now().strftime("%Y-%m-%d")


def _append_equity(rows: list[dict]) -> None:
    """One line per trading day: the portfolio value and its total P/L."""
    today = _tehran_today()
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
    HISTORY.write_text("\n".join(out[-400:]) + "\n", encoding="utf-8")


def build() -> dict:
    cfg = _cfg()
    tp = float(cfg.get("target_pct") or 25.0)
    sl = float(cfg.get("stop_pct") or 3.0)
    manual = _manual()
    broker_avg = _broker_avg()
    ledger_avg = _ledger_avg()

    rows = []
    for p in panel_positions():
        sym = p["symbol"]
        if sym in EXCLUDED_SYMBOLS:
            continue   # fully excluded from the list, the loop and every total
        qty = int(p["qty"])
        price = float(p["price"])
        broker_pnl_pct = p.get("pnl_pct")
        broker_pnl_rial = p.get("pnl_rial")
        broker_avg = float(p.get("avg_cost") or 0)
        if sym in manual:
            avg, src = manual[sym], "manual"
        elif broker_avg > 0:
            avg, src = broker_avg, "broker"
        elif broker_avg.get(sym, 0) > 0:
            avg, src = broker_avg[sym], "broker"
        elif ledger_avg.get(sym, 0) > 0:
            avg, src = ledger_avg[sym], "ledger"
        else:
            avg, src = 0.0, "unknown"
        # display the fee-inclusive basis so that price/avg-1 == the shown P/L
        _fee_avg = float(p.get("avg_cost_fee") or 0)
        if _fee_avg > 0:
            avg = _fee_avg
        # the panel's own P/L is authoritative when present (it is fee-inclusive)
        pnl = float(broker_pnl_pct) if broker_pnl_pct is not None else (((price / avg) - 1) * 100 if avg > 0 else 0.0)
        rows.append({
            "symbol": sym,
            "isin": p.get("isin"),
            "qty": qty,
            "avg_cost": round(avg, 1),
            "avg_source": src,
            "price": round(price, 1),
            "price_source": "panel",
            "value": round(qty * price, 0),
            "invested": round(qty * avg, 0) if avg else 0,
            "pnl_pct": round(pnl, 2),
            "pnl_rial": float(broker_pnl_rial) if broker_pnl_rial is not None else (round((price - avg) * qty, 0) if avg else 0),
            "avg_cost_fee": p.get("avg_cost_fee"),
            "avg_cost_raw": round(float(p.get("avg_cost") or 0), 1),
            "pnl_source": "broker_advanced" if broker_pnl_pct is not None else "computed",
            "take_profit_pct": tp,
            "stop_loss_pct": sl,
            "to_tp_pp": round(tp - pnl, 2),
            "to_sl_pp": round(pnl + sl, 2),
            "status": ("take_profit" if avg and pnl >= tp
                       else "stop_loss" if avg and pnl <= -sl else "hold"),
            "change_pct": p.get("change_pct"),
        })

    _append_equity(rows)
    out = {"at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
           "take_profit_pct": tp, "stop_loss_pct": sl,
           "formula": "pnl_pct = (price / avg_cost - 1) * 100",
           "positions": rows}
    # refuse to overwrite a good snapshot with an empty one (panel read failure)
    if not rows:
        try:
            prev = json.loads(OUT.read_text(encoding="utf-8"))
            if prev.get("positions"):
                print(json.dumps({"skipped": "empty panel read; previous snapshot kept"},
                                 ensure_ascii=False))
                return 0
        except Exception:
            pass
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    return out


if __name__ == "__main__":
    data = build()
    print(json.dumps(data, ensure_ascii=False)[:1500])
