"""Calibration journal: one row per trade, with the reason and the full context.

Every buy the bot submits writes an OPEN entry; every sell closes it. The file is
what a calibration study needs after a month:

    state/trade_journal.jsonl
        trade_id, symbol, side, at, price, qty, fee,
        reason, pick_rank, rank_score, volume, turnover, ceiling, session,
        status (open|closed), sell_at, sell_price, sell_fee, pnl_rial, pnl_pct,
        hold_days

`python trade_journal.py --sync` reconciles closes from state/realized.jsonl and
the broker ledger, then rewrites state/trade_journal.csv (Excel friendly).

It runs nightly with the scan (21:17) and is also called by the order flow right
after a successful buy.
"""
from __future__ import annotations

import csv
import json
import sys
from datetime import datetime
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

BASE = Path(r"C:\Users\Administrator\Desktop\DeepSeek DeskTop\WarRobot")
STATE = BASE / "state"
JOURNAL = STATE / "trade_journal.jsonl"
CSV_OUT = STATE / "trade_journal.csv"
CAND = STATE / "candidates.csv"
REALIZED = STATE / "realized.jsonl"
LEDGER = STATE / "trades_ledger.jsonl"

FIELDS = ["trade_id", "symbol", "at", "price", "qty", "fee", "reason",
          "pick_rank", "rank_score", "volume", "turnover", "ceiling", "session",
          "status", "sell_at", "sell_price", "sell_fee", "pnl_rial", "pnl_pct",
          "hold_days"]


def load() -> list[dict]:
    rows = []
    if JOURNAL.exists():
        for line in JOURNAL.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except Exception:
                    pass
    return rows


def save(rows: list[dict]) -> None:
    with JOURNAL.open("w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")


def candidate_context(symbol: str) -> dict:
    try:
        with CAND.open(encoding="utf-8-sig") as fh:
            for row in csv.DictReader(fh):
                if str(row.get("symbol")) == symbol:
                    return {"reason": row.get("reason"),
                            "pick_rank": row.get("pick_rank"),
                            "rank_score": row.get("rank_score"),
                            "volume": row.get("volume"),
                            "turnover": row.get("turnover"),
                            "ceiling": row.get("ceiling"),
                            "session": row.get("session")}
    except Exception:
        pass
    return {}


def record_buy(symbol: str, qty, price, fee: float = 0.0,
               reason: str = "", at: str | None = None) -> dict:
    rows = load()
    ctx = candidate_context(symbol)
    rec = {"trade_id": f"{datetime.now().strftime('%Y%m%d%H%M%S')}-{symbol}",
           "symbol": symbol,
           "at": at or datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
           "price": float(price or 0), "qty": float(qty or 0), "fee": float(fee or 0),
           "reason": reason or ctx.get("reason") or "",
           "pick_rank": ctx.get("pick_rank"), "rank_score": ctx.get("rank_score"),
           "volume": ctx.get("volume"), "turnover": ctx.get("turnover"),
           "ceiling": ctx.get("ceiling"), "session": ctx.get("session"),
           "status": "open", "sell_at": "", "sell_price": 0,
           "sell_fee": 0, "pnl_rial": 0, "pnl_pct": 0, "hold_days": 0}
    rows.append(rec)
    save(rows)
    return rec


def record_sell(symbol: str, qty, price, fee: float = 0.0,
                reason: str = "", at: str | None = None) -> dict:
    """Close the open journal row immediately after a successful sell."""
    rows = load()
    when = at or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    target = None
    for r in reversed(rows):
        if r.get("symbol") == symbol and r.get("status") == "open":
            target = r
            break
    if target is None:
        target = {"trade_id": f"orphan-{symbol}-{when[:10]}", "symbol": symbol,
                  "at": when, "price": float(price or 0), "qty": float(qty or 0),
                  "fee": 0.0, "reason": "بدون رکورد خرید (همگام‌سازی دستی)",
                  "pick_rank": "", "rank_score": "", "volume": "", "turnover": "",
                  "ceiling": "", "session": when[:10]}
        rows.append(target)
    buy_px = float(target.get("price") or 0)
    q = min(float(qty or target.get("qty") or 0), float(target.get("qty") or 0) or float(qty or 0))
    pnl = (float(price) - buy_px) * q - float(fee or 0)
    target.update({"status": "closed", "sell_at": when,
                   "sell_price": float(price or 0), "sell_fee": float(fee or 0),
                   "pnl_rial": round(pnl, 0),
                   "pnl_pct": round(pnl / (buy_px * q) * 100, 2) if buy_px * q else 0,
                   "hold_days": _days(str(target.get("at") or ""), when),
                   "sell_reason": reason})
    save(rows)
    export_csv()
    return target


def sync_closes() -> dict:
    """Close open entries from realised sales (and the broker ledger)."""
    rows = load()
    opened = {r["symbol"]: r for r in reversed(rows) if r.get("status") == "open"}
    closed = 0

    def do_close(sym: str, at: str, price: float, qty: float, fee: float, pnl_rial=None):
        nonlocal closed
        rec = opened.get(sym)
        if not rec or float(rec.get("qty") or 0) <= 0:
            return
        buy_px = float(rec.get("price") or 0)
        q = min(float(qty or rec["qty"]), float(rec["qty"]))
        pnl = pnl_rial if pnl_rial is not None else (price - buy_px) * q - fee
        rec.update({"status": "closed",
                    "sell_at": at, "sell_price": float(price or 0),
                    "sell_fee": float(fee or 0),
                    "pnl_rial": round(float(pnl or 0), 0),
                    "pnl_pct": round((float(pnl or 0) / (buy_px * q) * 100.0), 2)
                    if buy_px * q else 0,
                    "hold_days": _days(rec.get("at", ""), at)})
        opened.pop(sym, None)
        closed += 1

    if REALIZED.exists():
        for line in REALIZED.read_text(encoding="utf-8").splitlines():
            try:
                d = json.loads(line)
            except Exception:
                continue
            do_close(str(d.get("symbol") or ""), str(d.get("at") or "")[:19],
                     float(d.get("sell_price") or d.get("price") or 0),
                     float(d.get("qty") or 0), 0.0,
                     float(d.get("pnl_rial") or 0) or None)

    if LEDGER.exists():
        for line in LEDGER.read_text(encoding="utf-8").splitlines():
            try:
                d = json.loads(line)
            except Exception:
                continue
            if str(d.get("side")) != "sell":
                continue
            do_close(str(d.get("symbol") or ""), str(d.get("at") or "")[:19],
                     float(d.get("price") or 0), float(d.get("qty") or 0),
                     float(d.get("fee") or 0))

    save(rows)
    return {"journal_rows": len(rows),
            "open": sum(1 for r in rows if r.get("status") == "open"),
            "closed": closed,
            "closed_total": sum(1 for r in rows if r.get("status") == "closed")}


def _days(a: str, b: str) -> int:
    try:
        da = datetime.fromisoformat(a[:19])
        db = datetime.fromisoformat(b[:19])
        return max(0, (db - da).days)
    except Exception:
        return 0


def export_csv() -> int:
    rows = load()
    rows.sort(key=lambda r: str(r.get("at") or ""))
    with CSV_OUT.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in FIELDS})
    return len(rows)


def backfill() -> dict:
    """Seed the journal from realised sales and the current holdings."""
    rows = load()
    have = {(r.get("symbol"), str(r.get("at") or "")[:19], r.get("status")) for r in rows}
    added = 0

    # realised sales: buy row (from cost_value) + closed row
    if REALIZED.exists():
        for line in REALIZED.read_text(encoding="utf-8").splitlines():
            try:
                d = json.loads(line)
            except Exception:
                continue
            sym = str(d.get("symbol") or "")
            qty = float(d.get("qty") or 0)
            px = float(d.get("sell_price") or d.get("price") or 0)
            cost = float(d.get("cost_value") or 0)
            at = str(d.get("at") or "")[:19]
            pnl = float(d.get("pnl_rial") or 0)
            if not sym or qty <= 0:
                continue
            buy_px = (cost / qty) if cost and qty else 0.0
            key = (sym, at, "closed")
            if key in have:
                continue
            rows.append({
                "trade_id": f"backfill-{sym}-{at[:10]}", "symbol": sym,
                "at": at, "price": round(buy_px, 0), "qty": qty, "fee": 0.0,
                "reason": str(d.get("reason") or "فروش حد ضرر (تاریخی)"),
                "pick_rank": "", "rank_score": "", "volume": "", "turnover": "",
                "ceiling": "", "session": at[:10],
                "status": "closed", "sell_at": at, "sell_price": px,
                "sell_fee": 0.0, "pnl_rial": pnl,
                "pnl_pct": round(pnl / (buy_px * qty) * 100, 2) if buy_px * qty else 0,
                "hold_days": 0, "source": "realized_backfill"})
            have.add(key)
            added += 1

    # current holdings: open rows (no reason recorded historically)
    try:
        import json as _j
        pnl_doc = _j.loads((STATE / "pnl.json").read_text(encoding="utf-8"))
        for pos in pnl_doc.get("positions", []):
            sym = str(pos.get("symbol") or "")
            if sym in ("افران", "خساپا"):
                continue
            qty = float(pos.get("qty") or 0)
            avg = float(pos.get("avg_cost") or 0)
            if not sym or qty <= 0 or avg <= 0:
                continue
            key = (sym, "", "open")
            if any(r.get("symbol") == sym and r.get("status") == "open" for r in rows):
                continue
            rows.append({
                "trade_id": f"holding-{sym}", "symbol": sym,
                "at": (str(pnl_doc.get("at") or "")[:19]),
                "price": round(avg, 0), "qty": qty, "fee": 0.0,
                "reason": "پوزیشن موجود (از قبل، علت ثبت نشده)",
                "pick_rank": "", "rank_score": "", "volume": "", "turnover": "",
                "ceiling": "", "session": str(pnl_doc.get("at") or "")[:10],
                "status": "open", "sell_at": "", "sell_price": 0, "sell_fee": 0,
                "pnl_rial": 0, "pnl_pct": 0, "hold_days": 0,
                "source": "holdings_backfill"})
            added += 1
    except Exception:
        pass

    save(rows)
    return {"added": added, "rows": len(rows)}


def main() -> int:
    if "--backfill" in sys.argv:
        print(json.dumps(backfill(), ensure_ascii=False))
    if "--sync" in sys.argv or True:
        res = sync_closes()
        n = export_csv()
        res.update({"csv_rows": n, "csv": str(CSV_OUT),
                    "at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")})
        print(json.dumps(res, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
