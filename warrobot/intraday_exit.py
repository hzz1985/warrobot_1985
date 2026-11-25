"""Intraday take-profit / stop-loss watch — sells the moment a limit is hit.

Runs every few minutes between 09:00 and 12:30 (see scheduler.py REPEAT jobs).

For every open position it computes the live P/L against the position's TP/SL
(defaults from config: target_pct=25, stop_pct=3) and, when a limit is crossed,
places a sell order through the proven Playwright path (pw/flow.js ... sell),
then emails the operator with the result.

State: state/exits_log.jsonl (one line per attempted exit) and state/exit_state.json
(the last check, so the dashboard/daemon can show it).
"""
from __future__ import annotations

import pw_lock  # serialises Chrome on the shared profile

import json
import subprocess
import os
import sys
os.environ.setdefault("SESSION_FORCE_LOGIN", "1")
from datetime import datetime
from pathlib import Path

BASE = Path(r"C:\Users\Administrator\Desktop\DeepSeek DeskTop\WarRobot")
# fixed-income stock that receives every realised profit; never auto-sold
AFRAN_SYMBOL = "افران"
AFRAN_ISIN = "IRT1AFRN0001"
MANUAL_ONLY = {AFRAN_SYMBOL, "خساپا"}   # never auto-sold
sys.path.insert(0, str(BASE / "src"))
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE.parent / "Tse2" / "orderflow"))
sys.path.insert(0, str(BASE.parent / "TeseRobot" / "src"))
sys.path.insert(0, str(BASE.parent / "TeseRobot"))

import sys as _sys
try:
    _sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

NODE = r"C:\Program Files\nodejs\node.exe"
FEE = 0.005
PW = BASE / "pw"
LOG = BASE / "state" / "exits_log.jsonl"
STATE = BASE / "state" / "exit_state.json"
TARGETS = BASE / "state" / "targets.json"


def _config() -> dict:
    try:
        sell = json.loads((BASE / "config.json").read_text(encoding="utf-8"))["sell"]
    except Exception:
        return {"target_pct": 25.0, "stop_pct": 3.0, "max_hold_days": 15}
    try:  # strategy switch: v2 overrides the exit numbers, v1 returns them unchanged
        if str(BASE) not in sys.path:
            sys.path.insert(0, str(BASE))
        import strategy_v2 as _s2
        p = _s2.live_params(BASE / "config.json")
        if p["version"] == "v2":
            sell = {**sell, "target_pct": p["tp_pct"] or 1e9, "stop_pct": p["sl_pct"],
                    "max_hold_days": p["max_hold_days"]}
    except Exception:
        pass
    return sell


def _targets() -> dict:
    try:
        return json.loads(TARGETS.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _ledger() -> list[dict]:
    fp = BASE / "state" / "ledger.jsonl"
    if not fp.exists():
        return []
    rows = []
    for line in fp.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if line:
            try:
                rows.append(json.loads(line))
            except Exception:
                pass
    return rows


def positions() -> list[dict]:
    buys, sells = {}, {}
    for r in _ledger():
        sym, side = r.get("symbol"), r.get("side")
        if not sym:
            continue
        q, p = float(r.get("qty") or 0), float(r.get("price") or 0)
        if side == "buy":
            b = buys.setdefault(sym, {"qty": 0.0, "cost": 0.0, "ts": str(r.get("ts") or "")})
            b["qty"] += q
            b["cost"] += q * p
        elif side == "sell":
            sells[sym] = sells.get(sym, 0.0) + q
    out = []
    for sym, b in buys.items():
        qty = b["qty"] - sells.get(sym, 0.0)
        if qty <= 0:
            continue
        out.append({"symbol": sym, "qty": int(qty),
                    "avg": b["cost"] / b["qty"] if b["qty"] else 0.0,
                    "opened": b.get("ts", "")})
    return out


def live_price(symbol: str) -> float:
    """Best available live price: broker panel price first, TSETMC close second."""
    try:
        import tsetmc as api
        inst = api.resolve(symbol)
        if inst:
            raw = api.closing_daily(inst["ins_code"])
            rows = raw.get("closingPriceDaily") if isinstance(raw, dict) else None
            if rows:
                last = rows[-1]
                px = float(last.get("pClosing") or last.get("pDrCotVal") or 0)
                if px > 0:
                    return px
    except Exception:
        pass
    # fall back to the last sell-review snapshot
    try:
        import pandas as pd
        fp = BASE / "state" / "sell_candidates.csv"
        if fp.exists():
            df = pd.read_csv(fp)
            row = df[df["symbol"] == symbol]
            if len(row):
                return float(row["last"].iloc[0] or 0)
    except Exception:
        pass
    return 0.0


def _reference_price(symbol: str) -> float:
    """Last known market price (session close / panel) to validate an order price."""
    try:
        import json as _j
        for path in (_j and (BASE / "state" / "webdata" / "prices.json"),
                     BASE / "state" / "prices.json"):
            try:
                d = _j.loads(path.read_text(encoding="utf-8"))
                sym = (d.get("symbols") or {}).get(symbol) or {}
                px = float(sym.get("close") or 0)
                if px > 0:
                    return px
            except Exception:
                continue
    except Exception:
        pass
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


def transfer_profit_to_afran(profit_rial: float) -> dict:
    import profit_transfer
    return profit_transfer.transfer(profit_rial, source="intraday_exit")


def _legacy_transfer(profit_rial: float) -> dict:
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


def main(place: bool = True, notify: bool = True) -> int:
    import pw_lock as _pl
    with _pl.guard("intraday_exit") as got:
        if not got:
            print('{"skipped":"playwright_lock_timeout"}')
            return 0
        return _main_locked(place, notify)


def _main_locked(place: bool = True, notify: bool = True) -> int:
    # lot-based TP/SL first: each purchase is judged against its own cost
    try:
        import lot_exits
        lot_exits.main(dry=not place)
    except Exception as exc:
        print(f"lot_exits error: {str(exc)[:90]}", flush=True)

    cfg = _config()
    tp = float(cfg.get("target_pct") or 25.0)
    sl = float(cfg.get("stop_pct") or 3.0)
    tgt = _targets()
    out = {"at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "tp": tp, "sl": sl,
           "positions": [], "exits": []}

    # corrected source: panel prices + the same average cost as the dashboard
    pnl_rows = []
    try:
        import pnl as _pnl
        pnl_rows = _pnl.build().get("positions", [])
    except Exception:
        pnl_rows = []
    if pnl_rows:
        for row in pnl_rows:
            sym = row["symbol"]
            if sym in MANUAL_ONLY:
                continue
            out["positions"].append({**row})
            if row["status"] == "hold" or not place:
                continue
            # open_sell_guard: never stack a second sell order for the same symbol
            try:
                import cancel_orders as _co
                busy = {str(o.get("symbol")) for o in _co.open_orders()
                        if "فروش" in str(o.get("side") or "")}
            except Exception:
                busy = set()
            if sym in busy:
                out["positions"][-1]["note"] = "sell_order_already_open"
                continue
            px = float(row["price"])
            qty = int(row["qty"])
            avg = float(row.get("avg_cost") or 0)
            _ref = _reference_price(sym)
            if _ref > 0 and abs(px / _ref - 1) > 0.10:
                out["positions"][-1]["note"] = "exit_price_out_of_band"
                continue
            if avg and not (0.5 * avg <= px <= 2.0 * avg):
                out["positions"][-1]["note"] = "exit_price_out_of_band"
                continue
            try:
                import tsetmc as api
                inst = api.resolve(sym)
                isin = str((inst or {}).get("isin") or "")
            except Exception:
                isin = ""
            res = _sell(sym, isin, qty, px) if isin else {"message": "no_isin"}
            msg = str(res.get("message") or "")
            rec = {"at": out["at"], "symbol": sym, "qty": qty, "price": px,
                   "reason": "sell_tp" if row["status"] == "take_profit" else "sell_sl",
                   "pnl_pct": row["pnl_pct"], "message": msg,
                   "submitted": bool("موفق" in msg or "ثبت" in msg)}
            if rec["submitted"]:
                try:
                    import trade_journal as _tj
                    _tj.record_sell(sym, qty, px, reason=rec["reason"], at=rec["at"])
                    rec["journaled"] = True
                except Exception as exc:
                    rec["journal_error"] = str(exc)[:80]
            out["exits"].append(rec)
            with LOG.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        STATE.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
        if out["exits"] and notify:
            try:
                from tsebot.alerts import send
                lines = [f"زمان: {out['at']}", ""]
                for x in out["exits"]:
                    st = "✔ ثبت شد" if x["submitted"] else "✘ ثبت نشد"
                    lines.append(f"{x['symbol']} | {x['reason']} | {x['pnl_pct']:+.2f}% | "
                                 f"قیمت {x['price']:,.0f} | {st}")
                send("[WarRobot] خروج خودکار (حد سود/ضرر)", "\n".join(lines),
                     key="exit-auto", force=True)
            except Exception:
                pass
        triggered = [r["symbol"] for r in out["positions"] if r.get("status") in ("take_profit", "stop_loss")]
        print(json.dumps({"at": out["at"], "positions": len(out["positions"]),
                          "triggered": triggered, "exits": len(out["exits"])}, ensure_ascii=False))
        return 0

    for pos in positions():
        sym = pos["symbol"]
        if sym in MANUAL_ONLY:
            continue          # افران is never sold by the bot
        t = tgt.get(sym, {})
        p_tp = float(t.get("tp") or tp)
        p_sl = float(t.get("sl") or sl)
        px = live_price(sym)
        pnl = ((px / pos["avg"]) - 1) * 100 if pos["avg"] > 0 and px else 0.0
        decision = "hold"
        if px and pnl >= p_tp:
            decision = "sell_tp"
        elif px and pnl <= -p_sl:
            decision = "sell_sl"
        row = {"symbol": sym, "qty": pos["qty"], "avg": round(pos["avg"], 1),
               "price": px, "pnl_pct": round(pnl, 2), "tp": p_tp, "sl": p_sl,
               "decision": decision}
        out["positions"].append(row)

        if decision == "hold" or not place:
            continue
        try:
            import tsetmc as api
            inst = api.resolve(sym)
            isin = str((inst or {}).get("isin") or "")
        except Exception:
            isin = ""
        res = _sell(sym, isin, pos["qty"], px) if isin else {"message": "no_isin"}
        msg = str(res.get("message") or "")
        rec = {"at": out["at"], "symbol": sym, "qty": pos["qty"], "price": px,
               "reason": decision, "pnl_pct": row["pnl_pct"], "message": msg,
               "submitted": bool("موفق" in msg or "ثبت" in msg)}
        if rec["submitted"]:
            profit = (px - pos["avg"]) * pos["qty"] * (1 - FEE) if pos["avg"] else 0.0
            rec["afran_transfer"] = transfer_profit_to_afran(profit)
        out["exits"].append(rec)
        with LOG.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")

    STATE.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")

    if out["exits"] and notify:
        try:
            from tsebot.alerts import send
            lines = [f"زمان: {out['at']}", ""]
            for e in out["exits"]:
                status = "✔ ثبت شد" if e["submitted"] else "✘ ثبت نشد"
                lines.append(f"{e['symbol']} | {e['reason']} | سود/زیان {e['pnl_pct']:+.2f}% "
                             f"| قیمت {e['price']:,.0f} | {status}")
                if e["message"]:
                    lines.append(f"   پیام کارگزار: {e['message']}")
            send("[WarRobot] خروج خودکار (حد سود/ضرر)", "\n".join(lines),
                 key="exit-auto", force=True)
        except Exception:
            pass

    triggered = [r for r in out["positions"] if r["decision"] != "hold"]
    print(json.dumps({"at": out["at"], "positions": len(out["positions"]),
                      "triggered": [r["symbol"] for r in triggered],
                      "exits": len(out["exits"])}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(place="--dry" not in sys.argv))
