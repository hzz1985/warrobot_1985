"""Chunk BUY orders by the panel's آستانه حجم, mirroring the sell logic.

shares affordable = floor(cash / (price * (1 + buy_fee)))
chunks           = threshold-sized tickets, last one = remainder
"""
from pathlib import Path

P = Path(r"C:\Users\Administrator\Desktop\DeepSeek DeskTop\TeseRobot\src\tsebot\engine.py")
s = P.read_text(encoding="utf-8")

old_start = "        intent = self.orders.create(day, symbol, \"buy\", qty, price,"
i = s.index(old_start)
old_block = s[i:i + 1200]

new_block = '''        # split into tickets the broker accepts («آستانه حجم»), like the sell path
        max_q, min_q = 0, 1
        try:
            if hasattr(self.broker, "order_limits"):
                min_q, max_q = self.broker.order_limits()
        except Exception:
            max_q, min_q = 0, 1
        chunks: List[int] = []
        left = int(qty)
        while left > 0:
            n = min(left, max_q) if max_q > 0 else left
            if n < max(1, min_q):
                break
            chunks.append(n)
            left -= n
            if len(chunks) >= 20:
                break
        if not chunks:
            return {"ok": False, "symbol": symbol, "reason": "qty_below_minimum"}

        results: List[Dict[str, Any]] = []
        for n in chunks:
            intent = self.orders.create(day, symbol, "buy", n, price,
                                        reason=f"score={score:.4f}",
                                        extra={"score": score})
            if dry:
                self.orders.set_status(intent.ref, "cancelled", note="dry_run")
                results.append({"ok": True, "dry_run": True, "qty": n,
                                "price": price, "value_rial": n * price})
                continue
            res = self._submit_order("buy", symbol, n, price)
            not_placed = (res.get("placed") is False or res.get("sent") is False
                          or res.get("ok") is False
                          or (res.get("reason") and not res.get("sent") and not res.get("placed")))
            if not_placed:
                self.orders.set_status(intent.ref, "rejected", attempts=1,
                                       note=json.dumps(res, ensure_ascii=False)[:400])
                results.append({"ok": False, "qty": n, "price": price, "submit": res,
                                "reason": "not_placed"})
                break
            self.orders.set_status(intent.ref, "unverified", attempts=1,
                                   note=json.dumps(res, ensure_ascii=False)[:400])
            verified = self._verify_intent(intent, timeout=float(
                self.cfg.get("buy.verify_timeout_sec", 30)))
            results.append({"ok": bool(verified.get("ok")), "qty": n, "price": price,
                            "ref": intent.ref, "verify": verified, "submit": res})
            if not verified.get("ok"):
                break
        ok_all = all(r.get("ok") for r in results)
        filled = sum(r["qty"] for r in results if r.get("ok"))
        return {"ok": ok_all, "symbol": symbol, "qty": filtered_total(qty, filled),
                "filled": filled, "price": price, "chunks": chunks,
                "orders": results, "score": score}
''' + s[i + len(old_block):]

s = s[:i] + new_block
s = s.replace('qty": filtered_total(qty, filled),', 'qty": qty,')

# helper used above
if "def _affordable(" not in s:
    s = s.replace("    def execute_buy(", '''    def _affordable(self, cash: float, price: float) -> int:
        """Shares the available cash can buy, after the buy fee."""
        try:
            fee = 1.0 + float(self.cfg.get("fees.buy_pct", 1.5)) / 100.0
            return max(0, int(float(cash) / (float(price) * fee)))
        except Exception:
            return 0

    def execute_buy(''', 1)

P.write_text(s, encoding="utf-8")
print("execute_buy chunked")
