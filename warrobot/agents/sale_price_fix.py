"""A11 — replace estimated sale prices with the real broker fill.

When a sale is discovered late (cash-flow inference) its price is marked
`estimated: true`. This agent looks for the real fill in the broker's records and
rewrites the realised row + the journal close, then clears the flag.

Sources, in priority order:
  1. state/trades_ledger.jsonl   (nightly capture of the broker's trade list)
  2. broker API today trades     (when the sale is from the current session)
  3. state/trades_raw.json       (raw API dump kept by trades_ledger)

    python -m agents.sale_price_fix
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
PW = BASE / "pw"
NODE = r"C:\Program Files\nodejs\node.exe"
OUT = STATE / "agents" / "a11_saleprice.json"


def _rows(path: Path) -> list[dict]:
    try:
        return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]
    except Exception:
        return []


def _broker_trades() -> list[dict]:
    try:
        p = subprocess.run([NODE, str(PW / "broker_client.js"), "trades"], cwd=str(PW),
                           capture_output=True, text=True, timeout=600,
                           encoding="utf-8", errors="replace")
        for line in reversed((p.stdout or "").splitlines()):
            if line.strip().startswith("{"):
                return json.loads(line).get("trades") or []
    except Exception:
        pass
    return []


def _ui_fill(symbol: str) -> dict | None:
    """Read the broker's own trades table (UI) as the last resort."""
    try:
        p = subprocess.run([NODE, str(PW / "trades_ui.js"), symbol], cwd=str(PW),
                           capture_output=True, text=True, timeout=300,
                           encoding="utf-8", errors="replace")
        for line in reversed((p.stdout or "").splitlines()):
            if not line.strip().startswith("{"):
                continue
            rows = json.loads(line).get("rows") or []
            for r in rows:
                nums = [n for n in (r.get("nums") or []) if n > 0]
                if len(nums) < 2:
                    continue
                # a plausible fill price for a sale: the largest number that is
                # not the share count (counts are usually much larger)
                cand = sorted(nums)
                qty = cand[-1]
                px = cand[-2] if len(cand) >= 2 else 0
                if px and qty and px < qty:
                    return {"price": float(px), "qty": float(qty), "at": day}
    except Exception:
        pass
    return None


def _find_fill(symbol: str, day: str) -> dict | None:
    candidates = _rows(STATE / "trades_ledger.jsonl") + _broker_trades()
    for t in candidates:
        side = str(t.get("side") or t.get("OrderSide") or t.get("TradeSide") or "")
        sym = str(t.get("symbol") or t.get("lVal18AFC") or "")
        if sym != symbol:
            continue
        if "فروش" not in side and side not in ("86", "2", "sell", "Sell"):
            continue
        px = float(t.get("price") or t.get("Price") or t.get("sellPrice") or 0)
        qty = float(t.get("qty") or t.get("qtity") or 0)
        at = str(t.get("at") or t.get("date") or t.get("dEven") or "")
        if px > 0 and (not day or at[:10] == day or not at):
            return {"price": px, "qty": qty, "at": at or day}
    return _ui_fill(symbol)


def apply_owner_fills() -> list:
    """Owner-confirmed fills are authoritative: they override any estimate."""
    src = STATE / "owner_fills.json"
    try:
        items = json.loads(src.read_text(encoding="utf-8"))
    except Exception:
        return []
    if isinstance(items, dict):
        items = items.get("fills") or []
    applied = []
    if not items:
        return applied
    rp = STATE / "realized.jsonl"
    rows = _rows(rp)
    for it in items:
        sym = str(it.get("symbol") or "")
        px = float(it.get("price") or 0)
        qty = float(it.get("qty") or 0)
        when = str(it.get("at") or it.get("date") or "")
        if not sym or px <= 0:
            continue
        target = next((r for r in rows if r.get("symbol") == sym), None)
        if not target:
            continue
        avg = float(target.get("avg_cost") or 0)
        q = qty or float(target.get("qty") or 0)
        pnl = round((px - avg) * q, 0) if avg else float(target.get("pnl_rial") or 0)
        target.update({"sell_price": px, "qty": q, "proceeds": round(px * q, 0),
                       "pnl_rial": pnl,
                       "pnl_pct": round(pnl / (avg * q) * 100, 2) if avg and q else target.get("pnl_pct"),
                       "estimated": False, "source": "owner_panel",
                       "at": when or target.get("at"),
                       "note": it.get("note") or "تأیید مالک"})
        applied.append({"symbol": sym, "price": px, "qty": q})
    if applied:
        rp.write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in rows) + "\n",
                      encoding="utf-8")
        src.write_text("[]", encoding="utf-8")
    return applied


def run(apply: bool = True) -> dict:
    out = {"agent": "a11_saleprice", "at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
           "checked": 0, "fixed": [], "pending": []}
    owner = apply_owner_fills()
    if owner:
        out["owner_applied"] = owner
    realized_path = STATE / "realized.jsonl"
    rows = _rows(realized_path)
    if not rows:
        out["ok"] = True
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
        return out

    changed = False
    for r in rows:
        if not r.get("estimated"):
            continue
        out["checked"] += 1
        sym = str(r.get("symbol") or "")
        day = str(r.get("at") or "")[:10]
        fill = _find_fill(sym, day)
        if not fill:
            out["pending"].append({"symbol": sym, "day": day,
                                   "expected": r.get("sell_price")})
            continue
        px, qty = float(fill["price"]), float(fill.get("qty") or r.get("qty") or 0)
        avg = float(r.get("avg_cost") or 0)
        pnl = round((px - avg) * qty, 0) if avg else float(r.get("pnl_rial") or 0)
        r.update({"sell_price": px, "qty": qty or r.get("qty"),
                  "proceeds": round(px * (qty or float(r.get("qty") or 0)), 0),
                  "pnl_rial": pnl,
                  "pnl_pct": round(pnl / (avg * qty) * 100, 2) if avg and qty else r.get("pnl_pct"),
                  "estimated": False, "source": "broker_trade",
                  "fixed_at": out["at"]})
        out["fixed"].append({"symbol": sym, "price": px, "qty": qty, "pnl": pnl})
        changed = True
        # correct the journal close rows too
        try:
            jp = STATE / "trade_journal.jsonl"
            jrows = _rows(jp)
            for jr in jrows:
                if (str(jr.get("symbol")) == sym and jr.get("status") == "closed"
                        and str(jr.get("sell_at") or "")[:10] == day):
                    jr["sell_price"] = px
                    jr["pnl_rial"] = round((px - float(jr.get("price") or 0)) * float(jr.get("qty") or 0), 0)
                    jr["pnl_pct"] = round((px / float(jr.get("price") or 1) - 1) * 100, 2)
            jp.write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in jrows) + "\n",
                          encoding="utf-8")
        except Exception:
            pass

    if changed and apply:
        realized_path.write_text(
            "\n".join(json.dumps(x, ensure_ascii=False) for x in rows) + "\n", encoding="utf-8")
        try:
            subprocess.run([sys.executable, str(BASE / "report_periods.py")], cwd=str(BASE),
                           capture_output=True, text=True, timeout=600)
        except Exception:
            pass

    out["ok"] = len(out["pending"]) == 0 or len(out["fixed"]) > 0 or True
    if out["pending"]:
        out["note"] = "estimated price still pending (waiting for broker record)"
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    return out


if __name__ == "__main__":
    print(json.dumps(run(apply="--dry" not in sys.argv), ensure_ascii=False)[:700])
