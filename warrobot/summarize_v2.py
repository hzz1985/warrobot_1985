"""Summarise the v2 sweep: top configurations and the honest verdict table."""
import json
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

p = Path(r"C:\Users\Administrator\Desktop\DeepSeek DeskTop\WarRobot\data\options_clean\backtest_v2.json")
d = json.loads(p.read_text(encoding="utf-8"))
grid = d["grid"]


def key(r):
    return (r["median_ret_pct"], r["profit_factor"] or 0)


rows = sorted(grid, key=key, reverse=True)
print("=== بهترین ۸ ترکیب بر اساس میانهٔ بازده ===")
for r in rows[:8]:
    c = r["config"]
    print(f"{c['rule']}{'S' if c['strict'] else ' '} hold={c['hold']} stop={c['stop']:+.2f} | "
          f"tr={r['trades']:5d} med={r['median_ret_pct']:+6.2f}% "
          f"pf={r['profit_factor']} mdd={r['max_drawdown_pct']:6.1f}% "
          f"posy={r['positive_years_pct']:.0f}% total={r['total_return_pct']:+.0f}%")

print("\n=== گیت‌های پذیرش (همه باید پاس شوند) ===")
print("trades>=300 · median>0 · PF>1.3 · |MDD|<30% · سال‌های مثبت>=60%")
print("تعداد ترکیب‌های پاس‌شده:", d["verdict"]["configs_passing"], "از", d["verdict"]["configs_tested"])

best_c = [r for r in grid if r["config"]["rule"] == "C" and r["config"]["strict"]]
if best_c:
    b = max(best_c, key=key)
    print("\nبهترین C (strict):", json.dumps(b, ensure_ascii=False)[:700])
