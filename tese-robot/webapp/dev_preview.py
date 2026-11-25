# -*- coding: utf-8 -*-
"""Local preview of the dashboard without the Windows server.

Builds a throw-away WarRobot folder from the repo's history/ samples and starts
the Flask app against it (WARROBOT_DIR). Production is not affected: without
WARROBOT_DIR the app keeps reading the real C:\\...\\WarRobot folder.

    python dev_preview.py                 # history/ samples only
    python dev_preview.py --demo          # + a few SAMPLE files (positions, picks, balance)
    python dev_preview.py --port 5080

The --demo files are illustrative only (marked "sample"); they exist so every
section of the page can be seen while designing.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
HISTORY = REPO / "history"
AGENT_FILES = {"latest.json", "a10_sales.json", "a11_saleprice.json", "a13_slots.json",
               "a14_portfolio_pnl.json", "a15_sold_pnl.json"}


def build(target: Path, demo: bool) -> Path:
    state = target / "state"
    (state / "agents").mkdir(parents=True, exist_ok=True)
    (state / "webdata").mkdir(parents=True, exist_ok=True)
    for f in HISTORY.iterdir():
        dst = state / "agents" / f.name if f.name in AGENT_FILES else state / f.name
        shutil.copy2(f, dst)
    cfg = REPO / "warrobot" / "config.json"
    if cfg.exists():
        shutil.copy2(cfg, target / "config.json")
    if demo:
        _demo(state)
    return target


def _demo(state: Path) -> None:
    pos = [
        {"symbol": "آباد", "qty": 629746, "avg_cost": 2657.0, "price": 2721.0, "pnl_pct": 2.41},
        {"symbol": "آسیا", "qty": 180000, "avg_cost": 5390.0, "price": 5550.0, "pnl_pct": 2.97},
        {"symbol": "آریا", "qty": 14500, "avg_cost": 68900.0, "price": 67420.0, "pnl_pct": -2.15},
    ]
    for p in pos:
        p["value"] = p["qty"] * p["price"]
        p["invested"] = p["qty"] * p["avg_cost"]
        p["pnl_rial"] = p["value"] - p["invested"]
        p["status"] = "hold"
    pos.append({"symbol": "افران", "qty": 1200, "avg_cost": 19000, "price": 19110, "value": 22932000,
                "invested": 22800000, "pnl_pct": 0.58, "pnl_rial": 132000})
    (state / "pnl.json").write_text(json.dumps({"at": "2026-11-25 12:30:00", "sample": True,
                                                 "positions": pos}, ensure_ascii=False), encoding="utf-8")
    (state / "webdata" / "balance.json").write_text(json.dumps(
        {"at": "2026-11-25 12:30:00", "ok": True, "source": "sample",
         "account_balance": 412_500_000, "purchasing_power": 412_500_000}), encoding="utf-8")
    cands = ("pick_rank,symbol,close,next_ceiling,rank_score,reason,session\n"
             "1,آ س پ,18230,19140,6,قفل صف خرید + حجم بالا,1405-09-04\n"
             "2,آریا,65260,68520,5,BFomul: شکست مقاومت,1405-09-04\n"
             "3,آسیا,5290,5550,4,صف خرید سه روز متوالی,1405-09-04\n")
    (state / "candidates.csv").write_text(cands, encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--demo", action="store_true")
    ap.add_argument("--port", type=int, default=5077)
    ap.add_argument("--dir", default="")
    a = ap.parse_args()
    target = Path(a.dir) if a.dir else Path(tempfile.mkdtemp(prefix="warrobot_preview_"))
    build(target, a.demo)
    os.environ["WARROBOT_DIR"] = str(target)
    os.environ["TSEBOT_WEB_PORT"] = str(a.port)
    os.environ.setdefault("TZ", "Asia/Tehran")
    print("preview state:", target)
    import runpy
    runpy.run_path(str(Path(__file__).with_name("app.py")), run_name="__main__")


if __name__ == "__main__":
    main()
