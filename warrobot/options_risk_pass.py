"""Risk-control pass: does position sizing bring the best v3 config under the gates?

The best v3 config (rule C, hold=2, stop=-0.12) has a healthy median (+1.96%) and
PF 2.3 but a 43.8% drawdown and only 194 trades. More position slots (smaller
size per trade) should both add trades and shrink the drawdown percentage.
"""
import json
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

sys.path.insert(0, r"C:\Users\Administrator\Desktop\DeepSeek DeskTop\WarRobot")
import options_backtest_v3 as v3  # noqa: E402

OUT = Path(r"C:\Users\Administrator\Desktop\DeepSeek DeskTop\WarRobot\data\options_clean\risk_pass.json")


def main() -> int:
    contracts = v3.load_contracts()
    underlyings = v3.load_underlyings()
    print(f"contracts={len(contracts):,} underlyings={len(underlyings):,}")

    rows = []
    for maxpos in (3, 4, 5, 6, 8):
        v3.MAXPOS = maxpos
        for rule in ("C", "A"):
            for hold in (2, 3):
                for stop in (-0.08, -0.12):
                    r = v3.run(contracts, underlyings, rule, hold, stop, False)
                    r["maxpos"] = maxpos
                    r["passes"] = v3.gates(r)
                    rows.append(r)
                    flag = "PASS" if r["passes"] else "...."
                    print(f"{flag} slots={maxpos} {rule} h={hold} s={stop:+.2f} "
                          f"tr={r['trades']:5d} med={r['median_ret_pct']:+6.2f}% "
                          f"pf={r['profit_factor']} mdd={r['max_drawdown_pct']:6.1f}% "
                          f"posy={r['positive_years_pct']:.0f}%")

    passed = [r for r in rows if r["passes"]]
    OUT.write_text(json.dumps({"at": __import__("datetime").datetime.now().isoformat(),
                               "results": rows,
                               "passed": len(passed)},
                              ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\nconfigs passing gates: {len(passed)} / {len(rows)}")
    if passed:
        best = max(passed, key=lambda r: r["median_ret_pct"])
        print("BEST PASSING:", json.dumps({k: best[k] for k in (
            "config", "maxpos", "trades", "win_rate", "median_ret_pct",
            "total_return_pct", "max_drawdown_pct", "profit_factor",
            "positive_years_pct", "exit_mix")}, ensure_ascii=False))
    print("saved:", OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
