"""Add order_limits() and best_bid_ask() parsed from the tab-2 order panel."""
from pathlib import Path

P = Path(r"C:\Users\Administrator\Desktop\DeepSeek DeskTop\TeseRobot\src\tsebot\broker_selenium.py")
s = P.read_text(encoding="utf-8")

HELPERS = r'''
    def order_limits(self) -> tuple:
        """(min, max) shares per order from the panel's «آستانه حجم» row.

        The broker caps every ticket at a symbol-specific maximum (300,000 for وایرا
        on 2026-10-21); exceeding it makes the ticket fail silently.
        """
        import re as _re
        try:
            txt = self.page_text() or ""
        except Exception:
            return (1, 0)
        m = _re.search(r"آستانه\s*حجم[^\d]{0,6}([\d,]+)\s*[-–]\s*([\d,]+)", txt)
        if not m:
            return (1, 0)
        lo = int(m.group(1).replace(",", ""))
        hi = int(m.group(2).replace(",", ""))
        return (max(lo, 1), hi)

    def best_bid_ask(self) -> tuple:
        """(best_bid, best_ask) from the depth table printed on the order panel."""
        import re as _re
        try:
            txt = self.page_text() or ""
        except Exception:
            return (0.0, 0.0)
        bids, asks = [], []
        for line in txt.splitlines():
            parts = line.replace("\t", " ").replace("|", " ").split()
            nums = [p for p in parts if _re.fullmatch(r"[\d,]{3,}", p)]
            if len(nums) >= 3:
                try:
                    vals = [float(x.replace(",", "")) for x in nums]
                except Exception:
                    continue
                # depth rows look like: n  vol  bid  ask  vol  n
                for i in range(len(vals) - 1):
                    a, b = vals[i], vals[i + 1]
                    if 1000 <= a <= 1e7 and 1000 <= b <= 1e7:
                        if a < b:
                            bids.append(a)
                            asks.append(b)
                        break
        return (max(bids) if bids else 0.0, min(asks) if asks else 0.0)

    def page_text(self) -> str:
'''

if "def order_limits(self)" not in s:
    anchor = "    def page_text(self) -> str:"
    s = s.replace(anchor, HELPERS, 1)
    P.write_text(s, encoding="utf-8")
    print("helpers added")
else:
    print("already present")
