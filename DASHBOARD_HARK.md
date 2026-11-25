# بازطراحی داشبورد WarRobot

## چه چیزی عوض شد
- **ظاهر یکپارچه:** همهٔ لایه‌های CSS/JS که با اسکریپت‌های پچ (`apply_theme.py`، `apply_light.py`، `polish_chart.py`، `add_animations.py` و …) روی هم تلنبار شده بودند، در دو فایل جمع شدند:
  `templates/_dashboard_css.html` و `templates/_dashboard_js.html`.
- **فارسی و راست‌به‌چپ** با فونت Vazirmatn (از CDN؛ اگر در دسترس نبود Tahoma/فونت سیستم)، تاریخ‌ها **شمسی** با `/`.
- **تم روشن/تیره** با دکمهٔ ماه در بالای صفحه (انتخاب در مرورگر ذخیره می‌شود؛ پیش‌فرض = تم گوشی).
- **مناسب موبایل (اندروید):** کارت‌های دوستونه، تب‌های قابل‌اسکرول، جدول‌ها روی موبایل به کارت تبدیل می‌شوند، بدون اسکرول افقی.
- **تب جدید «نمای کلی»:**
  - کارت‌های اصلی: کل دارایی، سود/زیان امروز، سود/زیان محقق‌شده، تعداد پوزیشن باز، نقد/قدرت خرید
  - **سلامت ربات:** ورود کارگزار، سفارش پیش‌گشایش (order_truth)، چک‌لیست آمادگی، خطای ایجنت‌ها، کارهای امروز زمان‌بند (jobs_done)
  - پوزیشن‌های باز با **نوار فاصله تا حد ضرر/حد سود**
  - **منحنی دارایی** (Chart.js؛ اگر CDN باز نشد، نمودار سادهٔ داخلی)
  - کاندیدهای امروز
- **تب‌های جدید:** «سفارش‌ها» (orders_log + exits_log) و «ژورنال» (trade_journal). تب DeepSeekExport دوباره نمایش داده می‌شود (Copy/Delete کار می‌کند).
- تب‌های قبلی (خرید، فروش، گزارش، ایجنت‌ها) با همان داده‌ها حفظ شدند.
- اسنپ‌شات‌های دارایی تقریباً صفر (وقتی ورود کارگزار قطع است) در منحنی و KPI استفاده نمی‌شوند و هشدار داده می‌شود. «سود/زیان امروز» فقط وقتی نشان داده می‌شود که اسنپ‌شات امروز معتبر باشد.

## تغییرات بک‌اند (حداقلی)
- `warrobot_data.py`: مسیر WarRobot از متغیر محیطی `WARROBOT_DIR` (پیش‌فرض همان `C:\Users\Administrator\Desktop\DeepSeek DeskTop\WarRobot`) + تابع‌های فقط‌خواندنی `dashboard()`، `health()`، `orders_log()`، `journal()`، `equity_series()`.
- `app.py`: مسیرهای سخت‌کد WarRobot به `Wdata.STATE` / `Wdata.WAR` تغییر کرد؛ مسیر `/health` فعال شد؛ خطای `/demo` رفع شد. همهٔ مسیرهای قبلی (`/`، `/simple`، `/demo`، `/api/buy`، `/api/sell`، `/export/add`، `/export/delete`) سر جایشان هستند.
- `dev_preview.py`: اجرای محلی با نمونه‌های پوشهٔ `history/` (با `--demo` چند دادهٔ نمونه هم اضافه می‌شود).

## استقرار روی سرور
1. ربات را متوقف نکنید؛ فقط وب‌اپ عوض می‌شود.
2. در پوشهٔ TeseRobot روی سرور، این فایل‌ها را جایگزین/اضافه کنید:
   `webapp/app.py`، `webapp/warrobot_data.py`، `webapp/templates/index.html`،
   `webapp/templates/_dashboard_css.html`، `webapp/templates/_dashboard_js.html` (و اختیاری `webapp/dev_preview.py`).
3. `pm2 restart tse-web`
4. بررسی: `http://<server>:5077/health` و صفحهٔ اصلی (یک بار Ctrl+F5).
5. بازگشت: `git checkout 36dd8b0 -- tese-robot/webapp` و دوباره `pm2 restart tse-web`.

> ⚠ اسکریپت‌های پچ قدیمی (`apply_theme.py`، `apply_light.py`، `polish_chart.py`، `add_animations.py`، `new_chart.py`، `fix_positions.py`، `add_export_tab.py`، `rebuild_prompt_tab.py`، `remove_prompt_tab.py`) را **دوباره اجرا نکنید**؛ قالب جدید را خراب می‌کنند.

## اجرای محلی
```
pip install flask pandas numpy jdatetime
cd tese-robot/webapp
python dev_preview.py --demo --port 5080
```
