"""Rebuild TeseRobot/data/today_picks.json from WarRobot's nightly candidates.

The dashboard's "کاندیدهای خرید" tab reads that file, and it was frozen at
2026-10-20 — this job is what makes the tab update together with the nightly
scan. It runs from WarRobot's nightly pipeline (web_refresh.py) right after
queue_scan.py has written state/candidates.csv.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

WAR = Path(r"C:\Users\Administrator\Desktop\DeepSeek DeskTop\WarRobot")
TESE = Path(r"C:\Users\Administrator\Desktop\DeepSeek DeskTop\TeseRobot")
CAND = WAR / "state" / "candidates.csv"
OUT = TESE / "data" / "today_picks.json"


def main() -> int:
    if not CAND.exists():
        print("no candidates.csv")
        return 1

    import pandas as pd

    df = pd.read_csv(CAND)
    picks = []
    for i, r in enumerate(df.itertuples(index=False), start=1):
        row = r._asdict()
        close = row.get("close")
        ceiling = row.get("next_ceiling") or row.get("ceiling")
        try:
            dist = round((float(ceiling) / float(close) - 1) * 100, 2) if close else 0.0
        except Exception:
            dist = 0.0
        picks.append({
            "symbol": str(row.get("symbol")),
            "rank": int(row.get("pick_rank") or i),
            "score": float(row.get("rank_score") or 0) / 10.0,
            "feature_source": "queue-scan",
            "degraded": False,
            "dist_to_ceiling_pct": dist,
            "queue_run_today": True,
            "reason": str(row.get("reason") or ""),
        })

    payload = {
        "schema": "warrobot-v1",
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "source": "WarRobot/queue_scan.py",
        "mode": "live",
        "model": "queue-lock-v5",
        "label": "کاندیدهای خرید فردا (اسکن شبانه)",
        "data_day_jal": str(df["session"].iloc[0]) if "session" in df else "-",
        "for_date_g": datetime.now().strftime("%Y-%m-%d"),
        "picks": picks,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"today_picks.json rebuilt: {len(picks)} picks -> {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
