# TeseRobot — راهنمای عملیات

نسخهٔ مستقل و قابل اجرای استراتژی «صف خرید با دوام» (queue_v3).
همه‌چیز درون همین پوشه است: کد، مدل، دیتای روزانه، جدول‌های مرجع، بک‌تست‌ها و گزارش‌ها.

```
TeseRobot/
  src/tsebot/           کد ربات
  scripts/              run.py, update_data.py, build_day_features.py, make_paper_snapshot.py, ...
  config/config.json    همهٔ تنظیمات
  models/queue_v3.pkl   مدل آموزش‌دیده
  data/daily/           ۷۹۰ سری روزانه (OHLCV)
  data/reference/       جدول‌های فیچر مرجع (درون‌روز + Burst)
  data/train/           دیتاست آموزش (۱۸۷ هزار ردیف)
  features/             فیچرهای ساخته‌شده برای هر روز
  reports/              بک‌تست‌ها و گزارش‌ها
  docs/                 بررسی، معماری، استراتژی، این راهنما
  state/  logs/         وضعیت اجرا و لاگ‌ها
```

## ۱. زمان‌بندی روزانه (تهران)

| ساعت | کار | اسکریپت |
|---|---|---|
| ۲۰:۳۰ | دانلود OHLCV امروز | `update_data.py` |
| ۲۱:۰۰ | ساخت فیچر درون‌روز + Burst برای ۱۲۰ کاندید | `build_day_features.py --top 120` |
| ۲۱:۲۰ | ساخت پیک فردا با queue_v3 | `run.py picks` |
| ۰۸:۲۰ | اتصال، سینک فیل‌ها، مغایرت‌گیری | `preflight` |
| ۰۸:۴۰ | پلن حراج آغازین (خرید + خروج) | `preopen plan` |
| ۰۸:۳۰–۱۲:۲۸ | حلقهٔ فروش (هر ۶۰ ثانیه) | `sell_cycle` |
| ۰۸:۴۵، ۰۹:۰۰، … | نوبت‌های خرید | `buy_cycle` |
| ۱۳:۱۰ | گزارش بسته‌شدن | `close report` |

## ۲. اجرا

```powershell
$py = "C:\TSE_Strategy\venv\Scripts\python.exe"
$R  = "C:\Users\Administrator\Desktop\DeepSeek DeskTop\TeseRobot"

# زنجیرهٔ شبانه به‌صورت دستی
& $py "$R\scripts\run.py" picks
& $py "$R\scripts\build_day_features.py" --top 120
& $py "$R\scripts\update_data.py"

# دیمن (پیش‌فرض dry-run)
& $py "$R\scripts\run.py" daemon

# یا با اسکریپت آماده
& "$R\scripts\run_daemon.bat"
& "$R\scripts\run_nightly.bat"
```

## ۳. بررسی آماده بودن برای فردا

```powershell
& $py "$R\scripts\check_ready.py"
```

خروجی باید برای همهٔ موارد `PASS` بدهد: تاریخ آخرین کندل، وجود مدل، فیچرهای امروز،
پیک با `for_date_g` فردا، و نبود فایل توقف.

## ۴. توقف اضطراری

ساخت فایل `state/STOP` ربات را در همان تیک متوقف می‌کند (بدون kill کردن پروسه):

```powershell
New-Item -ItemType File "$R\state\STOP"
Remove-Item "$R\state\STOP"     # ادامه
```

## ۵. حالت‌ها

- `mode = "dry_run"` (پیش‌فرض): تصمیم‌ها گرفته و لاگ می‌شوند، سفارشی به کارگزار نمی‌رود.
- `mode = "live"`: علاوه بر تغییر کانفیگ، اجرای دیمن باید با `--i-understand-risk` باشد.
- `signals.require_full_features = true`: فقط پیک‌هایی معامله می‌شوند که فیچر درون‌روزی کامل دارند.

## ۶. بک‌تست‌ها

```powershell
# استراتژی صف با خروج queue-mode (روی دادهٔ روزانه)
& $py "$R\scripts\run.py" backtest --start 1404-06-01

# آزمون اقتصادی مدل با ری‌فیت walk-forward
& $py "$R\scripts\backtest_model.py" 0.5 daily 60
& $py "$R\scripts\backtest_model.py" 0.5 full 25

# کنترل تصادفی
& $py "$R\scripts\run.py" backtest --start 1404-06-01 --select random
```

نتایج در `reports/` ذخیره می‌شوند.

## ۷. بازآموزی مدل

```powershell
& $py "$R\scripts\build_training_set.py"
& $py "$R\scripts\train_queue_model.py"
```

`build_training_set.py` به جدول مرجع درون‌روز و Burst در `data/reference/` نیاز دارد.

## ۸. نکات عملیاتی

- Chrome ربات باید با `--remote-debugging-port=9222` و پروفایل `ChromeBotProfile` باز و لاگین باشد.
- قبل از اجرای live، تسک‌های نسخهٔ قبلی (`TSE_AutoBuy`, `TSE_S1_Nightly`, `BourseML_Hourly`) باید غیرفعال شوند تا دو ربات هم‌زمان سفارش ندهند.
- هر روز `state/` را بکاپ بگیرید (دفتر حسابداری و قصدهای سفارش).
