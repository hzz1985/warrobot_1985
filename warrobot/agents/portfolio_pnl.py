"""A14 — per-symbol P&L of the open portfolio, read from the broker panel.

The panel is the authority (it applies the broker's fee basis). This agent keeps
its own snapshot so the web app always has a fresh, timestamped copy.

    python -m agents.portfolio_pnl
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
STATE = BASE / "state"
OUT = STATE / "agents" / "a14_portfolio_pnl.json"
WEB = STATE / "webdata" / "portfolio_pnl.json"


def run() -> dict:
    # SINGLE_SOURCE: read exactly what the buy/sell tabs read (state/pnl.json),
    # so the agents tab can never disagree with them again.
    try:
        snap = json.loads((STATE / "pnl.json").read_text(encoding="utf-8"))
    except Exception:
        snap = {}
    rows, fund = [], None
    for p in snap.get("positions") or []:
        item = {"symbol": p.get("symbol"), "qty": p.get("qty"),
                "avg": p.get("avg_cost_fee") or p.get("avg_cost"),
                "price": p.get("price"), "value": p.get("value"),
                "pnl_rial": p.get("pnl_rial"), "pnl_pct": p.get("pnl_pct")}
        if str(p.get("symbol")) == "افران":
            fund = dict(item, note="صندوق درآمد ثابت — جزو پوزیشن‌ها نیست")
        elif str(p.get("symbol")) != "خساپا":
            rows.append(item)
    total_pnl = round(sum(float(r.get("pnl_rial") or 0) for r in rows), 0)
    total_val = round(sum(float(r.get("value") or 0) for r in rows), 0)
    out = {"agent": "a14_portfolio_pnl",
           "at": snap.get("at") or datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
           "source": "pnl.json (same as buy/sell tabs)",
           "positions": rows, "fund": fund,
           "total_pnl_rial": total_pnl, "total_value": total_val, "ok": True}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    WEB.parent.mkdir(parents=True, exist_ok=True)
    WEB.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    return out


def _old_run() -> dict:
    try:
        import agents.data_agents as da
        da.d2_portfolio()                    # refresh the panel snapshot first
    except Exception:
        pass
    rows, fund = [], None
    try:
        snap = json.loads((STATE / "webdata" / "portfolio.json").read_text(encoding="utf-8"))
        for p in snap.get("positions") or []:
            item = {"symbol": p.get("symbol"), "qty": p.get("qty"),
                    "avg": p.get("avg_cost_fee") or p.get("avg_cost"),
                    "price": p.get("price"), "value": p.get("value"),
                    "pnl_rial": p.get("pnl_fee_rial") or p.get("pnl_rial"),
                    "pnl_pct": p.get("pnl_fee_pct") or p.get("pnl_pct")}
            if str(p.get("symbol")) == "افران":
                fund = dict(item, note="صندوق درآمد ثابت — جزو پوزیشن‌ها نیست")
            elif str(p.get("symbol")) != "خساپا":
                rows.append(item)
    except Exception:
        pass
    total_pnl = round(sum(float(r.get("pnl_rial") or 0) for r in rows), 0)
    total_val = round(sum(float(r.get("value") or 0) for r in rows), 0)
    out = {"agent": "a14_portfolio_pnl",
           "at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
           "positions": rows, "fund": fund,
           "total_pnl_rial": total_pnl, "total_value": total_val,
           "ok": True}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    WEB.parent.mkdir(parents=True, exist_ok=True)
    WEB.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    return out


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False)[:700])
