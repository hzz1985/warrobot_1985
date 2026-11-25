"""Period reports (1M / 3M / 6M / 9M / 12M) for the web app's گزارش tab.

Numbers come from the closed trades only:
    pnl_rial = (sell_price - buy_price) * qty - commissions
The daily bookkeeping files are:
    state/trades_ledger.jsonl   one row per buy/sell with time, price, qty, fee
    state/realized.jsonl        realised sales written by the selling paths
    state/bot_start.json        the day this bot version started trading

A period becomes "available" when the bot has been running for at least that
many days; before that the chart stays empty with the remaining days shown.
Every run also reconciles the ledger total against realized.jsonl; a mismatch is
reported in the payload (and shown in the tab) instead of silently drawing a
wrong curve.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

BASE = Path(r"C:\Users\Administrator\Desktop\DeepSeek DeskTop\WarRobot")
STATE = BASE / "state"
LEDGER = STATE / "trades_ledger.jsonl"
REALIZED = STATE / "realized.jsonl"
START = STATE / "bot_start.json"
OUT = STATE / "report.json"

PERIODS = [("m1", 30, "۱ ماهه"), ("m3", 90, "۳ ماهه"), ("m6", 180, "۶ ماهه"),
           ("m9", 270, "۹ ماهه"), ("y1", 365, "۱ ساله")]


def load_jsonl(path: Path) -> list[dict]:
    rows = []
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except Exception:
                pass
    return rows


def bot_start() -> datetime:
    if START.exists():
        try:
            d = json.loads(START.read_text(encoding="utf-8"))
            return datetime.fromisoformat(d.get("start"))
        except Exception:
            pass
    first = None
    for r in load_jsonl(LEDGER) + load_jsonl(REALIZED):
        ts = str(r.get("at") or r.get("ts") or "")[:19]
        try:
            d = datetime.fromisoformat(ts)
        except Exception:
            continue
        if first is None or d < first:
            first = d
    start = first or datetime.now()
    START.write_text(json.dumps({"start": start.strftime("%Y-%m-%dT%H:%M:%S"),
                                 "note": "first trade recorded by this bot version"},
                                ensure_ascii=False, indent=1), encoding="utf-8")
    return start


def closed_trades() -> list[dict]:
    """Flatten buys/sells into closed trades with an explicit pnl."""
    rows = load_jsonl(LEDGER)
    buys: dict[str, list[dict]] = {}
    trades = []
    for r in rows:
        side = str(r.get("side") or "").lower()
        sym = str(r.get("symbol") or "")
        qty = float(r.get("qty") or 0)
        px = float(r.get("price") or 0)
        fee = float(r.get("fee") or 0)
        at = str(r.get("at") or "")
        if side == "buy":
            buys.setdefault(sym, []).append({"qty": qty, "price": px, "at": at, "fee": fee})
        elif side == "sell" and qty > 0 and px > 0:
            pool = buys.get(sym) or []
            buy_cost = 0.0
            left = qty
            while left > 0 and pool:
                lot = pool[0]
                take = min(left, lot["qty"])
                buy_cost += take * lot["price"] + (lot["fee"] * take / lot["qty"] if lot["qty"] else 0)
                lot["qty"] -= take
                left -= take
                if lot["qty"] <= 0:
                    pool.pop(0)
            if buy_cost <= 0:
                buy_cost = qty * float(r.get("buy_price") or 0)
            pnl = qty * px - buy_cost - fee
            trades.append({"symbol": sym, "at": at, "qty": qty, "price": px,
                           "buy_cost": buy_cost, "fee": fee, "pnl_rial": pnl})
    # realised.jsonl is authoritative for older sales (e.g. آردینه)
    for r in load_jsonl(REALIZED):
        at = str(r.get("at") or "")[:19]
        sym = str(r.get("symbol") or "")
        if not at or not sym:
            continue
        if any(t["symbol"] == sym and t["at"][:19] == at for t in trades):
            continue
        qty = float(r.get("qty") or 0)
        px = float(r.get("price") or 0)
        pnl = float(r.get("pnl_rial") or 0)
        if pnl == 0 and qty and px:
            pnl = qty * px - float(r.get("cost_value") or 0)
        trades.append({"symbol": sym, "at": at, "qty": qty, "price": px,
                       "buy_cost": float(r.get("cost_value") or 0), "fee": 0.0,
                       "pnl_rial": pnl, "source": "realized"})
    trades.sort(key=lambda t: t["at"])
    return trades


def main() -> int:
    start = bot_start()
    now = datetime.now()
    trades = closed_trades()
    payload = {"generated_at": now.strftime("%Y-%m-%d %H:%M:%S"),
               "bot_start": start.strftime("%Y-%m-%d"),
               "days_running": (now - start).days,
               "periods": {}}

    for key, days, label in PERIODS:
        end = start + timedelta(days=days)
        available = now >= end
        window_start = end - timedelta(days=days)
        in_window = [t for t in trades
                     if start <= datetime.fromisoformat(t["at"][:19]) <= min(now, end)]
        cum = 0.0
        points = []
        for t in in_window:
            cum += t["pnl_rial"]
            points.append({"date": t["at"][:10], "symbol": t["symbol"],
                           "pnl_rial": t["pnl_rial"], "cum": cum})
        payload["periods"][key] = {
            "label": label,
            "available": available,
            "start": start.strftime("%Y-%m-%d"),
            "end": (min(now, end)).strftime("%Y-%m-%d"),
            "period_end": end.strftime("%Y-%m-%d"),
            "days_left": max(0, (end - now).days),
            "trades": len(in_window),
            "pnl_rial": round(cum, 0),
            "points": points,
        }

    # reconciliation: every broker sale must have a matching realised entry
    # (same symbol and day); missing broker rows are fine (API keeps today only)
    realized_idx = {}
    for r in load_jsonl(REALIZED):
        key = (str(r.get("symbol") or ""), str(r.get("at") or "")[:10])
        realized_idx[key] = float(r.get("pnl_rial") or 0)
    problems = []
    ledger_sales = [t for t in trades if t.get("source") != "realized"]
    for t in ledger_sales:
        key = (t["symbol"], t["at"][:10])
        if key in realized_idx:
            if abs(realized_idx[key] - t["pnl_rial"]) > 2_000_000:
                problems.append({"symbol": t["symbol"], "date": t["at"][:10],
                                 "broker": round(t["pnl_rial"], 0),
                                 "realised": round(realized_idx[key], 0)})
        else:
            problems.append({"symbol": t["symbol"], "date": t["at"][:10],
                             "broker": round(t["pnl_rial"], 0),
                             "realised": None})
    payload["reconciliation"] = {
        "ledger_trades": len(ledger_sales),
        "realized_trades": len(realized_idx),
        "problems": problems[:10],
        "ok": not problems,
        "note": ("no broker rows captured yet" if not ledger_sales
                 else "compared against realised sales"),
        "checked_at": now.strftime("%Y-%m-%d %H:%M:%S"),
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({"days_running": payload["days_running"],
                      "trades": len(trades),
                      "periods": {k: {"available": v["available"],
                                      "trades": v["trades"],
                                      "pnl": v["pnl_rial"]}
                                  for k, v in payload["periods"].items()},
                      "recon": payload["reconciliation"]["ok"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
