"""Calendar sync from the exchange's own index history (official priority).

The operator's rule: the official calendar wins. The most official source we can
actually reach is TSETMC's index history: a date has a trading session only if the
exchange traded that day. So:

  * weekdays Sat..Wed with NO session in the index history  -> holiday
  * a date listed as holiday that DOES appear in the index  -> conflict, the
    official record wins and the local entry is removed

The script never touches dates newer than yesterday (a future holiday must come
from the announced calendar), and logs every decision to state/calendar_sync.json.

    python -m agents.calendar_sync
"""
from __future__ import annotations

import json
import sys
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

import market_holidays as mh

OUT = BASE / "state" / "calendar_sync.json"
INDEX_URL = "https://cdn.tsetmc.com/api/Index/GetIndexB2History/32097828799138957"
H = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/157.0"}
TRADING_WEEKDAYS = {5, 6, 0, 1, 2}     # Sat..Wed
LOOKBACK_DAYS = 200


def index_sessions() -> set[date]:
    try:
        raw = urllib.request.urlopen(urllib.request.Request(INDEX_URL, headers=H),
                                     timeout=40).read()
        data = json.loads(raw)
    except Exception:
        return set()
    out = set()
    for row in data.get("indexB2") or []:
        d = str(row.get("dEven") or "")
        if len(d) == 8 and d.isdigit():
            try:
                out.add(date(int(d[:4]), int(d[4:6]), int(d[6:8])))
            except Exception:
                pass
    return out


def to_jalali(d: date) -> str:
    try:
        import jdatetime
        j = jdatetime.date.fromgregorian(date=d)
        return f"{j.year:04d}-{j.month:02d}-{j.day:02d}"
    except Exception:
        return ""


def run() -> dict:
    sessions = index_sessions()
    if not sessions:
        res = {"ok": False, "error": "index history unreachable"}
        OUT.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
        return res

    latest = max(sessions)
    today = date.today()
    start = max(min(sessions), today - timedelta(days=LOOKBACK_DAYS))

    holidays_now = set()
    data = mh._load()
    for y, lst in data.items():
        if y.isdigit():
            holidays_now.update(lst or [])

    added, conflicts, candidates = [], [], []
    d = start
    while d < today - timedelta(days=1):
        if d.weekday() in TRADING_WEEKDAYS and d not in sessions:
            j = to_jalali(d)
            candidates.append({"date": j, "gregorian": d.isoformat()})
            if j and j not in holidays_now:
                added.append(mh.add_checked(j))
        d += timedelta(days=1)

    # official priority: a listed holiday that actually traded must be removed
    for j in sorted(holidays_now):
        try:
            import jdatetime
            y, m, dd = [int(x) for x in j.split("-")]
            g = jdatetime.date(y, m, dd).togregorian()
        except Exception:
            continue
        if g in sessions:
            conflicts.append({"date": j, "gregorian": g.isoformat(),
                              "action": mh.remove(j)})

    res = {"ok": True, "at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
           "source": "TSETMC index history (official sessions)",
           "sessions_known": len(sessions), "latest_session": latest.isoformat(),
           "holiday_candidates": candidates[-30:],
           "added": added, "conflicts": conflicts}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    return res


if __name__ == "__main__":
    r = run()
    print(json.dumps({k: r.get(k) for k in ("ok", "sessions_known", "latest_session",
                                            "added", "conflicts")},
                     ensure_ascii=False)[:900])
