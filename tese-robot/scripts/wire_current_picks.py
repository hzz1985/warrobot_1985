"""Web app: show the CURRENT nightly candidates (candidates_next.csv) instead of the
stale queue_v3 picks file."""
from pathlib import Path

P = Path(r"C:\Users\Administrator\Desktop\DeepSeek DeskTop\TeseRobot\webapp\app.py")
s = P.read_text(encoding="utf-8")

HELPER = r'''
def current_picks() -> Dict[str, Any]:
    """Tonight's ride candidates, with the entry distance and lock flag.

    Falls back to the legacy picks file when the nightly scan has not run yet.
    """
    path = CFG.path("state_dir") / "candidates_next.csv"
    if path.exists():
        try:
            import pandas as _pd
            df = _pd.read_csv(path)
            rows = []
            for i, r in df.iterrows():
                close = float(r.get("close") or 0)
                ceil = float(r.get("ceiling_today") or 0)
                dist = ((ceil - close) / close * 100) if close else 0.0
                locked = int(close >= ceil * 0.999) if ceil else 0
                rows.append({"rank": i + 1, "symbol": str(r["symbol"]),
                             "score": float(r.get("prob") or 0),
                             "feature_source": "ride_lock", "degraded": False,
                             "dist_to_ceiling_pct": round(dist, 3),
                             "queue_run_today": locked})
            if rows:
                return {"generated_at": str(df["built_at"].iloc[0]) if "built_at" in df else "-",
                        "for_date_g": "next", "picks": rows, "source": "ride_locked"}
        except Exception:
            pass
    return _read_json(CFG.path("picks_dir") / "today_picks.json", {})

'''

if "def current_picks(" not in s:
    anchor = "def to_jalali(ts: str) -> str:"
    s = s.replace(anchor, HELPER + "\n" + anchor, 1)

s = s.replace('    picks = _read_json(CFG.path("picks_dir") / "today_picks.json", {})\n    target', '    picks = current_picks()\n    target')
s = s.replace('    picks = _read_json(CFG.path("picks_dir") / "today_picks.json", {})\n    return render_template("index.html", picks=picks, positions=open_positions(),',
              '    picks = current_picks()\n    return render_template("index.html", picks=picks, positions=open_positions(),')
P.write_text(s, encoding="utf-8")
print("current_picks wired:", "current_picks()" in s)
