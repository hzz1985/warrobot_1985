"""Inspect the raw TSETMC closing-daily row keys for an option contract."""
import json
import sys

sys.path.insert(0, r"C:\Users\Administrator\Desktop\DeepSeek DeskTop\Tse2\orderflow")
import tsetmc as api

MAP = r"C:\Users\Administrator\Desktop\DeepSeek DeskTop\Tse2\orderflow\instruments.json"
d = json.load(open(MAP, encoding="utf-8"))
name = next(k for k in d if k.startswith("ض"))
ic = d[name]["ins_code"]
res = api.closing_daily(ic, 4000)
rows = (res or {}).get("closingPriceDaily") or []
print("contract:", name, "rows:", len(rows))
if rows:
    print("keys:", sorted(rows[-1].keys()))
    print("last row:", json.dumps(rows[-1], ensure_ascii=False))
    print("first row:", json.dumps(rows[0], ensure_ascii=False))
