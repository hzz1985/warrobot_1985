"""How deep is the free Tehran options history (TSETMC CDL)?"""
import json
import sys
from collections import Counter

sys.path.insert(0, r"C:\Users\Administrator\Desktop\DeepSeek DeskTop\Tse2\orderflow")
import tsetmc as api

MAP = r"C:\Users\Administrator\Desktop\DeepSeek DeskTop\Tse2\orderflow\instruments.json"
d = json.load(open(MAP, encoding="utf-8"))

pref = Counter()
for name, info in d.items():
    pref[name[:1]] += 1
print("market labels:", Counter(v.get("market") for v in d.values()).most_common())

opts = [(n, v) for n, v in d.items() if n.startswith("ض") or n.startswith("ط")]
print("prefix ض/ط:", len(opts))
print("first chars:", pref.most_common(8))

# sample a few contracts spread across the alphabet
samples = ["ضهرم5019", "ضصاد5016", "ضذوب4002", "ضستا4026"]
samples = [s for s in samples if s in d]
for s in samples:
    ic = d[s]["ins_code"]
    res = api.closing_daily(ic, 4000)
    rows = []
    if isinstance(res, dict):
        rows = res.get("closingPriceDaily") or []
    if rows:
        dates = [str(r.get("dEven")) for r in rows]
        print(f"{s}: {len(rows)} sessions | {min(dates)} .. {max(dates)}")
    else:
        print(f"{s}: no rows -> {str(res)[:100]}")
