"""Pre-open exit sweep: sell anything that should have been sold but wasn't.

Rule (operator, 1405-08-22): if a position reached its take-profit or stop-loss
and was not sold for any reason, the sell order MUST be placed at the start of the
next market session, during the pre-open auction (08:45).

Runs at 08:45 before the buy job:
  1. read the broker's own P/L (advanced portfolio)
  2. position status take_profit / stop_loss -> needs to be sold
  3. skip MANUAL_ONLY (افران) and the excluded symbol (خساپا)
  4. skip symbols that already have an open sell order
  5. place the sell at the panel price, log it, email a summary
"""
from __future__ import annotations

import json
import os
import sys
os.environ.setdefault("SESSION_FORCE_LOGIN", "1")
from datetime import datetime
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

BASE = Path(r"C:\Users\Administrator\Desktop\DeepSeek DeskTop\WarRobot")
sys.path.insert(0, str(BASE / "src"))
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE.parent / "Tse2" / "orderflow"))
sys.path.insert(0, str(BASE.parent / "TeseRobot" / "src"))
sys.path.insert(0, str(BASE.parent / "TeseRobot"))

import json as _json  # noqa: E402

LOG = BASE / "state" / "exits_log.jsonl"
MANUAL_ONLY = {"افران", "خساپا"}


def _isin(symbol: str) -> str:
    try:
        import tsetmc as api
        inst = api.resolve(symbol)
        return str((inst or {}).get("isin") or "")
    except Exception:
        return ""


def _sell(symbol: str, isin: str, qty: int, price: float) -> dict:
    import subprocess
    try:
        p = subprocess.run([r"C:\Program Files\nodejs\node.exe",
                            str(BASE / "pw" / "flow.js"), symbol, isin, str(qty),
                            str(price), "0", "sell"],
                           cwd=str(BASE / "pw"), capture_output=True, text=True,
                           timeout=900, encoding="utf-8", errors="replace")
        for line in reversed((p.stdout or "").splitlines()):
            if line.strip().startswith("{"):
                return _json.loads(line)
    except Exception as exc:
        return {"message": f"error:{str(exc)[:60]}"}
    return {}


def main(dry: bool = False) -> int:
    import market_cal
    if not market_cal.market_day():
        print(_json.dumps({"skipped": market_cal.reason_closed()}, ensure_ascii=False))
        return 0

    import pnl
    data = pnl.build() or {}
    if not isinstance(data, dict):
        data = {}
    positions = data.get("positions") or []
    due = [p for p in positions
           if p.get("status") in ("take_profit", "stop_loss")
           and p["symbol"] not in MANUAL_ONLY]

    open_sells = set()
    try:
        import cancel_orders as co
        open_sells = {str(o.get("symbol")) for o in co.open_orders()
                      if "فروش" in str(o.get("side") or "")}
    except Exception:
        pass

    out = {"at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
           "due": [{"symbol": p["symbol"], "status": p["status"],
                    "pnl_pct": p["pnl_pct"], "qty": p["qty"]} for p in due],
           "placed": [], "skipped": []}

    for p in due:
        sym = p["symbol"]
        if sym in open_sells:
            out["skipped"].append({"symbol": sym, "why": "sell_order_already_open"})
            continue
        if dry:
            out["skipped"].append({"symbol": sym, "why": "dry"})
            continue
        isin = _isin(sym)
        if not isin:
            out["skipped"].append({"symbol": sym, "why": "no_isin"})
            continue
        res = _sell(sym, isin, int(p["qty"]), float(p["price"]))
        msg = str(res.get("message") or "")
        rec = {"at": out["at"], "symbol": sym, "qty": int(p["qty"]),
               "price": float(p["price"]), "reason": "preopen_" + str(p["status"]),
               "pnl_pct": p["pnl_pct"], "message": msg,
               "submitted": bool("موفق" in msg or "ثبت" in msg)}
        if rec["submitted"]:
            try:
                import trade_journal as _tj
                _tj.record_sell(sym, rec["qty"], rec["price"],
                                reason=rec["reason"], at=rec["at"])
                rec["journaled"] = True
            except Exception as exc:
                rec["journal_error"] = str(exc)[:80]
        # realised profit goes to افران (contract clause 6)
        if rec["submitted"]:
            try:
                import profit_transfer as _pt
                profit = (float(rec["price"]) - float(p.get("avg_cost") or 0)) * int(rec["qty"])
                rec["afran_transfer"] = _pt.transfer(profit, source="preopen_exit")
            except Exception as exc:
                rec["afran_error"] = str(exc)[:80]
        out["placed"].append(rec)
        with LOG.open("a", encoding="utf-8") as fh:
            fh.write(_json.dumps(rec, ensure_ascii=False) + "\n")

    (BASE / "state" / "preopen_exits.json").write_text(
        _json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")

    if out["placed"]:
        try:
            from tsebot.alerts import send
            lines = [f"زمان: {out['at']}", ""]
            for r in out["placed"]:
                lines.append(f"{r['symbol']} | {r['reason']} | {r['pnl_pct']:+.2f}% | "
                             f"{'✔ ثبت شد' if r['submitted'] else '✘ ثبت نشد'}")
            send("[WarRobot] فروش پیش‌گشایشی (خروج معوق)", "\n".join(lines),
                 key="preopen-exits", force=True)
        except Exception:
            pass

    print(_json.dumps({"due": len(due), "placed": len(out["placed"]),
                       "skipped": len(out["skipped"])}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(dry="--dry" in sys.argv))
