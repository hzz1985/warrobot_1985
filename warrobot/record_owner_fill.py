"""Record the operator-confirmed آواپارس fill: 152,251 @ 8,430 on 1405/08/27."""
import json
from datetime import datetime
from pathlib import Path

BASE = Path(r"C:\Users\Administrator\Desktop\DeepSeek DeskTop\WarRobot")
STATE = BASE / "state"
PRICE = 8430.0
QTY = 152251.0
WHEN = "2026-11-18 10:00:00"      # 1405/08/27 (session day)
AVG = 7736.0

# ---- realised row ---------------------------------------------------------
rows = []
for line in (STATE / "realized.jsonl").read_text(encoding="utf-8").splitlines():
    if line.strip():
        rows.append(json.loads(line))

fixed = 0
for r in rows:
    if r.get("symbol") == "آواپارس" and r.get("estimated"):
        avg = float(r.get("avg_cost") or AVG)
        qty = float(r.get("qty") or QTY)
        pnl = round((PRICE - avg) * qty, 0)
        r.update({"sell_price": PRICE, "qty": qty,
                  "proceeds": round(PRICE * qty, 0),
                  "cost_value": round(avg * qty, 0),
                  "pnl_rial": pnl,
                  "pnl_pct": round(pnl / (avg * qty) * 100, 2),
                  "estimated": False, "source": "owner_panel",
                  "at": WHEN,
                  "note": "تأیید مالک: ۱۵۲٬۲۵۱ سهم @ ۸٬۴۳۰ در ۱۴۰۵/۰۸/۲۷"})
        fixed += 1
(STATE / "realized.jsonl").write_text(
    "\n".join(json.dumps(x, ensure_ascii=False) for x in rows) + "\n", encoding="utf-8")
print("realised rows fixed:", fixed)
for r in rows:
    if r.get("symbol") == "آواپارس":
        print("  ->", r["sell_price"], r["qty"], r["pnl_rial"], r["pnl_pct"], r["source"])

# ---- journal closes -------------------------------------------------------
jp = STATE / "trade_journal.jsonl"
jrows = [json.loads(x) for x in jp.read_text(encoding="utf-8").splitlines() if x.strip()]
n = 0
for jr in jrows:
    if jr.get("symbol") == "آواپارس" and jr.get("status") == "closed":
        buy = float(jr.get("price") or 0)
        q = float(jr.get("qty") or 0)
        pnl = round((PRICE - buy) * q, 0)
        jr["sell_price"] = PRICE
        jr["sell_at"] = WHEN
        jr["pnl_rial"] = pnl
        jr["pnl_pct"] = round((PRICE / buy - 1) * 100, 2) if buy else 0.0
        jr["reason"] = "take_profit_confirmed"
        n += 1
jp.write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in jrows) + "\n", encoding="utf-8")
print("journal rows fixed:", n)
