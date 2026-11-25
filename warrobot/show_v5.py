"""Print the v5 decision cleanly (walk-forward, sensitivity, final verdict)."""
import json

p = r"C:\Users\Administrator\Desktop\DeepSeek DeskTop\WarRobot\data\options_clean\backtest_v5.json"
d = json.load(open(p, encoding="utf-8"))
dec = d["decision"]

print("=== FINAL DECISION ===")
print("accepted:", dec["accepted"])
print("reason :", dec["reason"])
print("checks :", json.dumps(dec["checks"], ensure_ascii=False))
print()
print("candidate :", json.dumps(dec["candidate"], ensure_ascii=False))
print("total     :", json.dumps(dec["total"], ensure_ascii=False))
print("in-sample :", json.dumps(dec["in_sample"], ensure_ascii=False))
print("out-sample:", json.dumps(dec["out_of_sample"], ensure_ascii=False))
print()
print("=== sensitivity: top 8 (median) ===")
for v in sorted(d["variants"], key=lambda x: -x["median_ret_pct"])[:8]:
    print(f"hold={v['hold']} stop={v['stop']:+.3f} {v['tier']:8s} "
          f"tr={v['trades']:4d} med={v['median_ret_pct']:+6.2f}% "
          f"pf={v['profit_factor']} mdd={v['max_drawdown_pct']:.1f}%")
print()
print("=== sensitivity: bottom 4 ===")
for v in sorted(d["variants"], key=lambda x: x["median_ret_pct"])[:4]:
    print(f"hold={v['hold']} stop={v['stop']:+.3f} {v['tier']:8s} "
          f"tr={v['trades']:4d} med={v['median_ret_pct']:+6.2f}% "
          f"pf={v['profit_factor']} mdd={v['max_drawdown_pct']:.1f}%")
