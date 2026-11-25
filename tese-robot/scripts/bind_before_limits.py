"""Bind the symbol before reading its per-order limit, in both order paths."""
from pathlib import Path

P = Path(r"C:\Users\Administrator\Desktop\DeepSeek DeskTop\TeseRobot\src\tsebot\engine.py")
s = P.read_text(encoding="utf-8")

old_exit = """        panel_max, panel_bid = 0, 0.0
        try:
            if hasattr(self.broker, "order_limits"):"""
new_exit = """        panel_max, panel_bid = 0, 0.0
        try:
            if hasattr(self.broker, "bind_symbol"):
                self.broker.bind_symbol(sym)          # per-symbol «آستانه حجم»
            if hasattr(self.broker, "order_limits"):"""

old_buy = """        max_q, min_q = 0, 1
        try:
            if hasattr(self.broker, "order_limits"):"""
new_buy = """        max_q, min_q = 0, 1
        try:
            if hasattr(self.broker, "bind_symbol"):
                self.broker.bind_symbol(symbol)       # per-symbol «آستانه حجم»
            if hasattr(self.broker, "order_limits"):"""

n = 0
if old_exit in s:
    s = s.replace(old_exit, new_exit, 1)
    n += 1
if old_buy in s:
    s = s.replace(old_buy, new_buy, 1)
    n += 1
P.write_text(s, encoding="utf-8")
print("patched", n, "call sites")
