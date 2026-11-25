"""Build $TSEDATA/clienttype/ins_map.json (insCode -> symbol) and $TSEDATA/codal/universe.json (symbols) from
the price panel: every symbol that closed ceiling-locked with value >= 10 bn rial since 2024-03 (the V2/V3
candidate pool).  Run once before fetch_clienttype.py / fetch_instinfo.py / fetch_rahavard_eps.py."""
import json, os, sys
import pandas as pd
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import backtest_v2 as B
TSEDATA = os.environ.get("TSEDATA", os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "tsedata"))
p = B.load_panel(os.path.join(TSEDATA, "panel.pkl"))
s = p[p.lock_up & (p.val >= 1e10) & (p.date >= "2024-03-01")]
u = sorted(s.sym.unique().tolist())
os.makedirs(f"{TSEDATA}/clienttype", exist_ok=True); os.makedirs(f"{TSEDATA}/codal", exist_ok=True)
json.dump(u, open(f"{TSEDATA}/codal/universe.json", "w"), ensure_ascii=False)
m = p[p.sym.isin(set(u))].groupby("ins").sym.last()
json.dump({str(k): v for k, v in m.items()}, open(f"{TSEDATA}/clienttype/ins_map.json", "w"), ensure_ascii=False)
print(len(u), "symbols,", len(m), "insCodes")
