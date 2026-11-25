from pathlib import Path
import sys

sys.path.insert(0, r"C:\Users\Administrator\Desktop\DeepSeek DeskTop\WarRobot")

# scheduler job
sc = Path(r"C:\Users\Administrator\Desktop\DeepSeek DeskTop\WarRobot\scheduler.py")
s = sc.read_text(encoding="utf-8")
if "sale_price_fix" not in s:
    s = s.replace('    ("21:16", "ledger", ["trades_ledger.py"]),',
                  '    ("21:16", "ledger", ["trades_ledger.py"]),\n'
                  '    ("21:18", "saleprice", ["agents/sale_price_fix.py"]),')
    s = s.replace('"sellrev", "weekly", "calendar", "calsync", "salesrec"}',
                  '"sellrev", "weekly", "calendar", "calsync", "salesrec", "saleprice"}')
    sc.write_text(s, encoding="utf-8")
    print("scheduler: a11 nightly 21:18")

# supervisor registration
sup = Path(r"C:\Users\Administrator\Desktop\DeepSeek DeskTop\WarRobot\agents\supervisor.py")
u = sup.read_text(encoding="utf-8")
if '"a11"' not in u:
    u = u.replace('"a10": "sale_reconcile"}',
                  '"a10": "sale_reconcile", "a11": "sale_price_fix"}')
    sup.write_text(u, encoding="utf-8")
    print("supervisor: a11 registered")

# selfcheck
r = Path(r"C:\Users\Administrator\Desktop\DeepSeek DeskTop\WarRobot\selfcheck_rules.py")
q = r.read_text(encoding="utf-8")
if "اصلاح قیمت تقریبی فروش" not in q:
    q = q.replace('    add("تطبیق شبانهٔ فروش‌ها با کارگزار (a10)",',
                  '    add("اصلاح قیمت تقریبی فروش با رکورد کارگزار (a11)",\n'
                  '        (BASE / "agents" / "sale_price_fix.py").exists(),\n'
                  '        "agents/sale_price_fix.py")\n'
                  '    add("تطبیق شبانهٔ فروش‌ها با کارگزار (a10)",')
    r.write_text(q, encoding="utf-8")
    print("rule added")

# lessons
from agents import lessons
lessons.record(
    "قیمت فروش برآوردی ماند (estimated)",
    "سابقهٔ تاریخ‌دار معاملات در API کارگزار نبود و رکورد با تغییر نقد بازسازی شد",
    "agents/sale_price_fix.py هر شب رکورد کارگزار را می‌گردد و قیمت برآوردی را با مبلغ واقعی جایگزین می‌کند",
    "state/agents/a11_saleprice.json → pending=[] و estimated=false در realized",
    ["sale", "estimated", "price", "فروش"],
    source="architect",
)
lessons.record(
    "bind نماد با نام دارای فاصله (آ س پ) شکست می‌خورد",
    "مقایسهٔ innerText === symbol؛ پیشنهادهای پنل قالب «آ س پ1 - آ.س.پ» دارند",
    "bind نرمال‌شده (حذف ZWNJ/فاصلهٔ اضافه) + انتخاب پیشنهاد با پیشوند و fallback شبکه‌ای + حافظهٔ bind",
    "FLOW_DRY=1 node flow.js 'آ س پ' ... → bound شامل «آ س پ»",
    ["bind", "panel", "symbol"],
    source="architect",
)
print("lessons recorded")
