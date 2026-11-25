"""Cap every buy by the LIVE buying power read from the panel before submission."""
from pathlib import Path

P = Path(r"C:\Users\Administrator\Desktop\DeepSeek DeskTop\TeseRobot\src\tsebot\engine.py")
s = P.read_text(encoding="utf-8")

anchor = """        # Split into tickets the panel accepts («آستانه حجم»), same rule as the sell
        # path: N-1 tickets of the maximum size plus one remainder ticket."""

cap = '''        # LIVE cash check: the blocked money from an unfilled ticket is released the
        # next session, so the affordable size must be recomputed at every attempt.
        try:
            power = float(self.broker.buying_power() or 0) if hasattr(self.broker, "buying_power") else 0.0
        except Exception:
            power = 0.0
        if power > 0:
            fee_mult = 1.0 + float(self.cfg.get("fees.buy_pct", 1.5)) / 100.0
            affordable = int(power / max(float(price) * fee_mult, 1.0))
            if qty > affordable:
                self.log(f"buy {symbol}: qty {qty:,} capped to {affordable:,} "
                         f"(power {power:,.0f} rial @ {price:,.0f})")
                qty = affordable
            else:
                self.log(f"buy {symbol}: qty {qty:,} within power {power:,.0f} rial @ {price:,.0f}")
            if qty <= 0:
                return {"ok": False, "symbol": symbol, "reason": "insufficient_cash",
                        "power_rial": power, "price": price}

'''

if "insufficient_cash" not in s:
    s = s.replace(anchor, cap + anchor, 1)
    P.write_text(s, encoding="utf-8")
    print("cash cap added")
else:
    print("already present")
