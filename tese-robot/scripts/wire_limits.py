"""Use the panel's آستانه حجم as the per-order cap and the depth best bid as price."""
from pathlib import Path

P = Path(r"C:\Users\Administrator\Desktop\DeepSeek DeskTop\TeseRobot\src\tsebot\engine.py")
s = P.read_text(encoding="utf-8")

old = """        max_q = int(getattr(info, "max_qty", 0) or 0) if info is not None else 0"""

new = """        # per-order cap from the panel («آستانه حجم») and the live best bid
        panel_max, panel_bid = 0, 0.0
        try:
            if hasattr(self.broker, "order_limits"):
                _lo, panel_max = self.broker.order_limits()
            if hasattr(self.broker, "best_bid_ask"):
                panel_bid, _ask = self.broker.best_bid_ask()
        except Exception:
            panel_max, panel_bid = 0, 0.0
        if panel_bid and not aggressive:
            price = float(panel_bid)
        max_q = int(getattr(info, "max_qty", 0) or 0) if info is not None else 0
        if max_q <= 0 and panel_max > 0:
            max_q = int(panel_max)"""

if old in s:
    s = s.replace(old, new, 1)
    P.write_text(s, encoding="utf-8")
    print("patched execute_exit")
else:
    print("pattern not found")
