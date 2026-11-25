import json

import trade_journal as tj

QTY = 152251.0
AVG_FEE = 7736.0
PRICE = 8356.0          # inferred from the cash change; marked as estimated
PNL = round((PRICE - AVG_FEE) * QTY, 0)

row = {
    "at": "2026-11-18 09:10:00",
    "symbol": "آواپارس",
    "qty": QTY,
    "avg_cost": AVG_FEE,
    "sell_price": PRICE,
    "cost_value": round(AVG_FEE * QTY, 0),
    "proceeds": round(PRICE * QTY, 0),
    "pnl_rial": PNL,
    "pnl_pct": round(PNL / (AVG_FEE * QTY) * 100, 2),
    "reason": "take_profit",
    "source": "cash_inferred",
    "estimated": True,
    "note": "قیمت فروش از روی تغییر نقد محاسبه شده (API تاریخ معاملات را نمی‌دهد)",
}
with open("state/realized.jsonl", "a", encoding="utf-8") as fh:
    fh.write(json.dumps(row, ensure_ascii=False) + "\n")
print("realized:", row["symbol"], row["qty"], row["sell_price"], row["pnl_rial"], row["pnl_pct"])

closed = 0
for lot in [r for r in tj.load() if r.get("symbol") == "آواپارس" and r.get("status") == "open"]:
    tj.record_sell("آواپارس", float(lot["qty"]), PRICE,
                   reason="take_profit_estimated", at="2026-11-18 09:10:00")
    closed += 1
print("journal lots closed:", closed)
