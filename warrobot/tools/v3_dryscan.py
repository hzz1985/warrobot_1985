"""Hark V3 dry scan: queue_scan selection + strategy_v3 filters, writes NOTHING into state/."""
import os, sys, json, inspect, textwrap
os.environ["FLOW_DRY"] = "1"
B = r"C:\Users\Administrator\Desktop\DeepSeek DeskTop\WarRobot"
sys.path.insert(0, B); os.chdir(B)
import pandas as pd
import queue_scan as q
out_dir = os.path.join(os.environ.get("TEMP", "."), "hark_v3_dry"); os.makedirs(out_dir, exist_ok=True)
_orig = pd.DataFrame.to_csv
def _to_csv(self, path=None, *a, **k):
    p = os.path.join(out_dir, os.path.basename(str(path)))
    return _orig(self, p, *a, **k)
pd.DataFrame.to_csv = _to_csv
src = textwrap.dedent(inspect.getsource(q.main))
src = src.split("    # nightly review")[0] + "    return 0\n"
ns = dict(vars(q)); exec(src, ns); ns["main"]()
c = pd.read_csv(os.path.join(out_dir, "candidates.csv"))
print("PICKS (rank order):")
cols = [x for x in ("symbol", "close", "turnover", "session", "pe", "growth", "bp", "inflow") if x in c.columns]
print(c[cols].to_string(index=False))
r = os.path.join(out_dir, "reserve.csv")
if os.path.exists(r): print("RESERVE:", list(pd.read_csv(r)["symbol"]))
