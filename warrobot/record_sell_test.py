"""Record the verified test sell and append the lesson to the knowledge base."""
import json
from datetime import datetime
from pathlib import Path

BASE = Path(r"C:\Users\Administrator\Desktop\DeepSeek DeskTop\WarRobot")

rec = {
    "at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    "symbol": "آردینه",
    "qty": 150,
    "price": 46380,
    "reason": "sell_test",
    "message": "سفارش فروش آردینه1 با موفقیت در سیستم ثبت شد.",
    "submitted": True,
    "note": "تست کوچک فروش از مسیر Playwright برای اثبات کارکرد فروش",
}
with (BASE / "state" / "exits_log.jsonl").open("a", encoding="utf-8") as fh:
    fh.write(json.dumps(rec, ensure_ascii=False) + "\n")

doc = BASE / "docs" / "RUNBOOK.md"
s = doc.read_text(encoding="utf-8")
s += '''
## ۱۲) تست اثبات‌شدهٔ فروش (۱۴۰۵-۰۸-۱۸)

### اجرا
```
pw/flow.js "آردینه" IRO3ARDZ0001 150 46380 0 sell
```
### نتیجه از پنل کارگزار
```
message: «سفارش فروش آردینه1 با موفقیت در سیستم ثبت شد.»
open   : آردینه | قیمت 46,380 | زمان 09:15:23 | دیتا 1405-08-18
```
### یعنی چه
- مسیر فروش (flow.js با آرگومان sell) **کار می‌کند** و کارگزار تأیید می‌دهد
- تعداد کم (۱۵۰ سهم) برای تست استفاده شد، بدون آسیب به استراتژی
- رکورد در `state/exits_log.jsonl` ثبت شد (reason = sell_test)

### درس برای مدل
```
۱) برای فروش، همان بایند دقیق لازم است (bind_mismatch جلوی نماد اشتباه را می‌گیرد)
۲) قیمت فروش = آخرین قیمت زنده؛ اگر خارج از دامنه باشد، پنل رد می‌کند
۳) تعداد فروش نباید از پوزیشن باز بیشتر باشد (وگرنه رد می‌شود)
۴) پس از ثبت، سفارش در فهرست سفارشات باز با سمت «فروش» دیده می‌شود
۵) حد سود/ضرر خودکار از همین مسیر استفاده می‌کند (intraday_exit.py)
```
'''
doc.write_text(s, encoding="utf-8")
print("test sell recorded and documented")
