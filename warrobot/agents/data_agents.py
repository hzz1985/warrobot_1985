"""Dedicated data-collection agents for the web app (one per card / tab item).

Each agent reads ONE broker page/tab and writes its own small file with a
timestamp, so every card can show its own "last update" and a failing source
never blanks the others:

    d1 balance    -> /Accounting/Remain            state/webdata/balance.json
    d2 portfolio  -> RealtimePortfolioAdvanced     state/webdata/portfolio.json
    d3 pnl        -> computed from d2 (+ledger)    state/webdata/pnl.json
    d4 prices     -> local daily files + panel     state/webdata/prices.json
    d5 sells      -> realised + journal            state/webdata/sells.json

Timers (scheduler):
    balance     every 5 min in market, 30 min otherwise
    portfolio   every 10 min in market, 60 min otherwise
    pnl         every 15 min in market, 60 min otherwise
    prices      08:22 and 21:18 daily
    sells       21:16 daily

    python -m agents.data_agents --job balance
    python -m agents.data_agents --job all
"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
STATE = BASE / "state"
WEB = STATE / "webdata"
PW = BASE / "pw"
NODE = r"C:\Program Files\nodejs\node.exe"
PY = sys.executable
WEB.mkdir(parents=True, exist_ok=True)


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _write(name: str, payload: dict) -> dict:
    payload.setdefault("at", _now())
    (WEB / f"{name}.json").write_text(json.dumps(payload, ensure_ascii=False, indent=1),
                                       encoding="utf-8")
    return payload


def _json_tail(text: str) -> dict:
    for line in reversed((text or "").splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line)
            except Exception:
                continue
    return {}


def d1_balance() -> dict:
    """Buying power card — source: broker accounting tab."""
    try:
        p = subprocess.run([NODE, str(PW / "balance.js")], cwd=str(PW),
                           capture_output=True, text=True, timeout=600,
                           encoding="utf-8", errors="replace")
        d = _json_tail(p.stdout + p.stderr)
    except Exception as exc:
        d = {"error": str(exc)[:100]}
    return _write("balance", {"source": "Accounting/Remain",
                              "account_balance": d.get("account_balance"),
                              "purchasing_power": d.get("purchasing_power"),
                              "blocked": d.get("blocked_balance"),
                              "ok": bool(d.get("account_balance"))})


def d2_portfolio() -> dict:
    """Positions + fund card — source: advanced realtime portfolio tab."""
    try:
        p = subprocess.run([NODE, str(PW / "advanced_portfolio_values.js")], cwd=str(PW),
                           capture_output=True, text=True, timeout=900,
                           encoding="utf-8", errors="replace")
        d = _json_tail(p.stdout + p.stderr)
        rows = d.get("positions") or []
    except Exception as exc:
        rows = []
        d = {"error": str(exc)[:100]}
    return _write("portfolio", {"source": "RealtimePortfolioAdvanced",
                                "positions": rows, "count": len(rows),
                                "ok": bool(rows)})


def d3_pnl() -> dict:
    """P&L card + curve — computed from the portfolio snapshot (no extra login)."""
    try:
        port = json.loads((WEB / "portfolio.json").read_text(encoding="utf-8"))
    except Exception:
        port = {}
    rows = []
    for p in port.get("positions") or []:
        if str(p.get("symbol")) == "خساپا":
            continue        # excluded from the bot's portfolio by policy
        price = float(p.get("price") or 0)
        avg = float(p.get("avg_cost_fee") or p.get("avg_cost") or 0)
        qty = float(p.get("qty") or 0)
        pnl_pct = ((price / avg - 1) * 100) if avg else 0.0
        rows.append({"symbol": p.get("symbol"), "qty": qty, "avg": round(avg, 0),
                     "price": round(price, 0), "value": round(qty * price, 0),
                     "pnl_pct": round(pnl_pct, 2),
                     "pnl_rial": round((price - avg) * qty, 0)})
    total = round(sum(r["pnl_rial"] for r in rows), 0)
    invested = round(sum(r["avg"] * r["qty"] for r in rows), 0) or 1
    try:
        hist = [json.loads(x) for x in
                (STATE / "equity_history.jsonl").read_text(encoding="utf-8").splitlines()
                if x.strip()]
    except Exception:
        hist = []
    # one equity point per day (the report/sell charts read this file)
    try:
        import market_cal
        day = market_cal.now_tehran().strftime("%Y-%m-%d")
        hist_today = [h for h in hist if str(h.get("date")) == day]
        if not hist_today:
            snap_bal = {}
            try:
                snap_bal = json.loads((WEB / "balance.json").read_text(encoding="utf-8"))
            except Exception:
                pass
            cash = float(snap_bal.get("account_balance") or 0)
            holdings = round(sum(r["value"] for r in rows), 0)
            with (STATE / "equity_history.jsonl").open("a", encoding="utf-8") as fh:
                fh.write(json.dumps({
                    "date": day, "at": datetime.now().strftime("%H:%M"),
                    "holdings_value": holdings, "cash": cash,
                    "total": round(holdings + cash, 0),
                    "pnl_rial": total, "pnl_pct": round(total / invested * 100, 2),
                    "source": "data_agents.equity_append"}, ensure_ascii=False) + "\n")
            payload["equity_append"] = "added"
    except Exception as exc:
        payload["equity_append_error"] = str(exc)[:80]

    payload = _write("pnl", {"source": "computed from portfolio snapshot",
                             "rows": rows, "total_pnl": total,
                             "total_pct": round(total / invested * 100, 2),
                             "equity": hist[-40:], "ok": bool(rows)})
    # keep the canonical pnl.json in sync for the bot's exit logic
    try:
        tp, sl = 25.0, 3.0
        try:
            cfg = json.loads((BASE / "config.json").read_text(encoding="utf-8"))
            tp = float(cfg.get("sell", {}).get("target_pct", 25.0))
            sl = float(cfg.get("sell", {}).get("stop_pct", 3.0))
        except Exception:
            pass
        canon = []
        for r in rows:
            status = ("take_profit" if r["pnl_pct"] >= tp
                      else "stop_loss" if r["pnl_pct"] <= -sl else "hold")
            canon.append({
                "symbol": r["symbol"], "qty": r["qty"], "avg_cost": r["avg"],
                "avg_cost_fee": r["avg"], "price": r["price"],
                "value": r["value"], "invested": round(r["avg"] * r["qty"], 0),
                "pnl_pct": r["pnl_pct"], "pnl_rial": r["pnl_rial"],
                "pnl_source": "portfolio_snapshot", "avg_source": "broker",
                "price_source": "panel", "take_profit_pct": tp,
                "stop_loss_pct": sl, "to_tp_pp": round(tp - r["pnl_pct"], 2),
                "to_sl_pp": round(r["pnl_pct"] + sl, 2), "status": status,
                "change_pct": None})
        (STATE / "pnl.json").write_text(json.dumps(
            {"at": payload["at"], "take_profit_pct": tp, "stop_loss_pct": sl,
             "formula": "pnl_pct = (price / avg_cost - 1) * 100",
             "positions": canon}, ensure_ascii=False, indent=1), encoding="utf-8")
        payload["pnl_positions"] = len(canon)
    except Exception as exc:
        payload["pnl_write_error"] = str(exc)[:80]
    return payload


def d4_prices() -> dict:
    """Buy-candidate prices card — source: local daily files + broker panel."""
    try:
        p = subprocess.run([PY, str(BASE / "nightly_prices.py")], cwd=str(BASE),
                           capture_output=True, text=True, timeout=1800,
                           encoding="utf-8", errors="replace")
        d = _json_tail(p.stdout)
    except Exception as exc:
        d = {"error": str(exc)[:100]}
    try:
        snap = json.loads((STATE / "prices.json").read_text(encoding="utf-8"))
    except Exception:
        snap = {}
    return _write("prices", {"source": "daily files + broker panel",
                             "symbols": snap.get("symbols") or {},
                             "balance": snap.get("balance") or {},
                             "ok": bool(snap.get("symbols"))})


def d5_sells() -> dict:
    """Sold symbols card — source: realised records + journal."""
    rows, realized = [], []
    try:
        for line in (STATE / "realized.jsonl").read_text(encoding="utf-8").splitlines():
            if line.strip():
                realized.append(json.loads(line))
    except Exception:
        pass
    try:
        for line in (STATE / "trade_journal.jsonl").read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                if r.get("status") == "closed":
                    rows.append({"symbol": r.get("symbol"), "qty": r.get("qty"),
                                 "buy": r.get("price"), "sell": r.get("sell_price"),
                                 "pnl_rial": r.get("pnl_rial"),
                                 "pnl_pct": r.get("pnl_pct"),
                                 "at": r.get("sell_at")})
    except Exception:
        pass
    return _write("sells", {"source": "realised + journal", "rows": rows,
                            "realized": realized[-10:], "ok": True})


def d6_sell() -> dict:
    """Sell tab: rows with status + the stats shown above the table."""
    try:
        pnl = json.loads((STATE / "pnl.json").read_text(encoding="utf-8"))
    except Exception:
        pnl = {}
    tp = float(pnl.get("take_profit_pct") or 25.0)
    sl = float(pnl.get("stop_loss_pct") or 3.0)
    rows = []
    for p in pnl.get("positions", []):
        pct = float(p.get("pnl_pct") or 0)
        if p.get("symbol") == "افران":
            continue
        rows.append({"symbol": p.get("symbol"), "qty": p.get("qty"),
                     "avg": p.get("avg_cost"), "price": p.get("price"),
                     "pnl_pct": pct,
                     "decision": "take_profit" if pct >= tp
                                 else "stop_loss" if pct <= -sl else "hold"})
    rows.sort(key=lambda r: -float(r.get("pnl_pct") or 0))
    sells = []
    try:
        for line in (STATE / "trade_journal.jsonl").read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                if r.get("status") == "closed":
                    sells.append({"symbol": r.get("symbol"),
                                  "pnl_pct": r.get("pnl_pct"),
                                  "pnl_rial": r.get("pnl_rial"),
                                  "at": r.get("sell_at")})
    except Exception:
        pass
    avg_sell = (sum(float(s["pnl_pct"] or 0) for s in sells) / len(sells)) if sells else None
    return _write("sell", {"source": "pnl.json + journal", "rows": rows,
                           "tp": tp, "sl": sl, "sells": sells[-6:],
                           "sell_count": len(sells),
                           "avg_sell_pct": round(avg_sell, 2) if avg_sell is not None else None,
                           "ok": bool(rows) or bool(sells)})


JOBS = {"sell": d6_sell, "balance": d1_balance, "portfolio": d2_portfolio, "pnl": d3_pnl,
        "prices": d4_prices, "sells": d5_sells}


def main() -> int:
    args = sys.argv[1:]
    job = "all"
    if "--job" in args:
        i = args.index("--job")
        if i + 1 < len(args):
            job = args[i + 1]
    names = list(JOBS) if job == "all" else [job]
    out = {}
    for n in names:
        fn = JOBS.get(n)
        if not fn:
            out[n] = {"ok": False, "error": "unknown job"}
            continue
        try:
            out[n] = {"ok": fn().get("ok")}
        except Exception as exc:
            out[n] = {"ok": False, "error": str(exc)[:100]}
    print(json.dumps({"at": _now(), "jobs": out}, ensure_ascii=False))
    return 0 if all(v.get("ok") for v in out.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
