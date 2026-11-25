"""Can TSETMC serve Tehran OPTIONS (derivatives) data? — direct probe."""
import json
import sys
import urllib.parse

sys.path.insert(0, r"C:\Users\Administrator\Desktop\DeepSeek DeskTop\Tse2\orderflow")
import tsetmc as api

BASE = "https://cdn.tsetmc.com/api"


def try_url(url: str):
    try:
        r = api._get(url, timeout=30)
        return r
    except Exception as exc:
        return {"error": str(exc)[:120]}


terms = ["ضصدرا", "ضهرم", "ضخودرو", "آپشن", "ض"]
for t in terms:
    enc = urllib.parse.quote(t)
    data = try_url(f"{BASE}/Instrument/GetInstrumentSearch/{enc}/1")
    rows = []
    if isinstance(data, dict):
        rows = (data.get("instrumentSearch") or data.get("InstrumentSearch")
                or data.get("data") or [])
    if isinstance(rows, list) and rows:
        print(f"\nterm={t}: {len(rows)} results")
        for r in rows[:5]:
            print("   ", r.get("lVal18AFC") or r.get("lVal18AFC"), "|",
                  r.get("insCode"), "|", (r.get("flowTitle") or r.get("flow") or ""))
    else:
        print(f"\nterm={t}: no results ({str(data)[:100]})")
