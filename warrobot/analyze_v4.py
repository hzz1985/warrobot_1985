"""Inspect the v4 grid: what blocks the gates, and the best near-miss."""
import json
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

p = r"C:\Users\Administrator\Desktop\DeepSeek DeskTop\WarRobot\data\options_clean\backtest_v4.json"
d = json.load(open(p, encoding="utf-8"))
grid = d["grid"]

print("total configs:", len(grid))


def ok(m, need_trades=300, mdd=30):
    return (m["trades"] >= need_trades and m["median_ret_pct"] > 0
            and (m["profit_factor"] or 0) > 1.3 and abs(m["max_drawdown_pct"]) < mdd
            and m["positive_years_pct"] >= 60)


for need, mdd in ((300, 30), (150, 30), (100, 30), (80, 30)):
    passed = [m for m in grid if ok(m, need, mdd)]
    print(f"\ngate trades>={need}, mdd<{mdd}%: {len(passed)} passing")
    if passed:
        b = max(passed, key=lambda m: (m["median_ret_pct"], m["profit_factor"] or 0))
        print("  best:", json.dumps({"cfg": b["config"], "trades": b["trades"],
                                     "median": b["median_ret_pct"],
                                     "pf": b["profit_factor"],
                                     "mdd": b["max_drawdown_pct"],
                                     "posy": b["positive_years_pct"],
                                     "exit": b["exit_mix"]}, ensure_ascii=False))

# risk-rules effect: same config with and without risk
pairs = {}
for m in grid:
    c = m["config"]
    key = (c["rule"], c["hold"], c["stop"], c["tier"])
    pairs.setdefault(key, {})[c["risk"]] = m
improved = []
for key, v in pairs.items():
    if True in v and False in v:
        a = v[False]["max_drawdown_pct"]
        b = v[True]["max_drawdown_pct"]
        improved.append((a - b, key, a, b))
improved.sort(reverse=True)
print("\nrisk rules effect (drawdown before -> after), best 5:")
for delta, key, a, b in improved[:5]:
    print(f"  {key}: {a:.1f}% -> {b:.1f}%  (improved {delta:.1f} pts)")
