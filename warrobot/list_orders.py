"""List the broker's open orders right now (symbol, qty, price, side, status)."""
import json
import re
import sys
from pathlib import Path

BASE = Path(r"C:\Users\Administrator\Desktop\DeepSeek DeskTop\WarRobot")
sys.path.insert(0, str(BASE / "src"))
sys.path.insert(0, str(BASE))

import broker_pw

d = broker_pw.check()
flat = json.dumps(d, ensure_ascii=False).replace("\\\\", "")
open_txt = ""
m = re.search(r'"open":\s*"', flat)
if m:
    open_txt = flat[m.end():]

orders = re.findall(
    r'"symbol":"([^"]{2,20})".*?"orderprice":([\d.]+).*?"orderid":"?(\d+)"?.*?"orderside":"([^"]+)".*?"qunatity":(\d+).*?"excuted":(\d+)',
    open_txt)
if not orders:
    print("no open orders")
else:
    for sym, price, oid, side, qty, ex in orders:
        print(f"{sym} | {side} | qty={qty} | price={float(price):,.0f} | executed={ex} | id={oid}")
