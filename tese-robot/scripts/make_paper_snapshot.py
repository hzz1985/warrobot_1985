# -*- coding: utf-8 -*-
"""Build a paper-broker snapshot from real daily closes, for a full simulation.

Example:
    python scripts\\make_paper_snapshot.py --cash 500000000 --position وایرا:3790:300000

Every symbol gets a plausible quote: last = latest close, ceiling/floor = ±limit,
best bid/ask = last ∓ 1 tick. The snapshot is what PaperBroker consumes, so the
whole buy/sell path (including verification through the order list) can be
exercised with zero market risk.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from tsebot import config as config_mod  # noqa: E402
from tsebot.signals import load_panel  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cash", type=float, default=500_000_000)
    ap.add_argument("--out", default=None)
    ap.add_argument("--limit", type=float, default=5.0)
    ap.add_argument("--symbols", default="", help="comma separated extras")
    ap.add_argument("--position", action="append", default=[],
                    help="symbol:entry_price:qty (repeatable) - adds a holding")
    args = ap.parse_args()

    cfg = config_mod.load()
    panel = load_panel(cfg.path("market_data_dir"))
    if not panel:
        print("no market data")
        return 1

    wanted = set(s for s in args.symbols.split(",") if s.strip())
    try:
        src = cfg.path("picks_dir") / "today_picks.json"
        if not src.exists():
            src = cfg.path("picks_dir") / "picks_v2.json"
        picks = json.loads(src.read_text(encoding="utf-8"))
        wanted |= {p["symbol"] for p in picks.get("picks", [])}
    except Exception:
        pass

    syms = {}
    for sym in sorted(wanted):
        df = panel.get(sym)
        if df is None:
            continue
        last = float(df["Close"].iloc[-1])
        lim = args.limit / 100.0
        syms[sym] = {
            "last": round(last, 1),
            "ceiling": round(last * (1 + lim), 1),
            "floor": round(last * (1 - lim), 1),
            "best_bid": round(max(1.0, last - 1), 1),
            "best_ask": round(last + 1, 1),
            "min_qty": 1,
            "max_qty": 5_000_000,
            "prev_close": round(float(df["Close"].iloc[-2]), 1) if len(df) > 1 else round(last, 1),
        }

    positions = {}
    cost_basis = {}
    for spec in args.position:
        try:
            sym, entry, qty = spec.split(":")
            positions[sym] = int(qty)
            cost_basis[sym] = float(entry)
            if sym not in syms:
                df = panel.get(sym)
                if df is not None:
                    last = float(df["Close"].iloc[-1])
                    syms[sym] = {
                        "last": round(last, 1),
                        "ceiling": round(last * 1.05, 1),
                        "floor": round(last * 0.95, 1),
                        "best_bid": round(max(1.0, last - 1), 1),
                        "best_ask": round(last + 1, 1),
                    }
        except Exception:
            print(f"bad --position {spec!r}")
            return 2

    snap = {"buying_power": args.cash, "positions": positions,
            "cost_basis": cost_basis, "symbols": syms}
    out = Path(args.out) if args.out else cfg.path("state_dir") / "paper_snapshot.json"
    out.write_text(json.dumps(snap, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"snapshot: {out}  symbols={len(syms)} positions={positions}")
    return 0


if __name__ == "__main__":
    sys.exit(main())


