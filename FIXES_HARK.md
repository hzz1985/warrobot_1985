# رفع سه باگ WarRobot (شاخهٔ `hark/fixes`)

پشتهٔ زنده: pm2 → `warrobot/daemon.py` (= `run.py daemon`) + `warrobot/scheduler.py` (warsched).
`tese-robot/` فقط به‌عنوان کتابخانه (`tsebot.alerts`، `calendar_ir.now_tehran`، `clock`) و وب‌اپ استفاده می‌شود.
شماره خط‌ها مربوط به نسخهٔ قبل از رفع (commit `36dd8b0`) است.

---

## باگ ۱ — سفارش پیش‌گشایش ۰۸:۴۵:۰۱ ثبت نمی‌شد

### علت‌ها (با شاهد)
1. **زمان‌بند هم‌زمان (synchronous) بود.** `scheduler.py:160-208` هر کار را با `subprocess.run` اجرا و صبر می‌کرد.
   در `history/jobs_done.json` (۱۴۰۵/۰۹/۰۱ تا ۰۹/۰۴) کار ۰۸:۴۰ (`preready`) حدود ۹ دقیقه طول کشید و در تیک ۰۸:۴۹
   کارهای `preflight → lotsell → preexit → preclean → pwreset → order` پشت سر هم اجرا شدند؛
   در `orders_log.jsonl` اولین سفارش ~۱۲ دقیقه بعد ثبت شده است.
2. **`pwreset` ساعت ۰۸:۴۴** (`scheduler.py:50`) کروم واردشده را درست قبل از سفارش می‌کشت → سفارش باید دوباره با کپچا لاگین می‌کرد.
3. **`run.py:387` در daemon هم** در ۰۸:۴۵ یک `order_candidates.py` دوم را هم‌زمان اجرا می‌کرد (همان پروفایل/نشست).
4. **قفل بی‌اثر:** `order_candidates.py:273-277` قفل را گرفته و بلافاصله آزاد می‌کرد؛ تمام کار شبکه‌ای (موجودی، سفارش باز، پرتفوی، TSETMC) **بعد از** ۰۸:۴۵:۰۱ انجام می‌شد.
5. **ساعت:** `now_tehran()` = ساعت سیستم + `system.clock_offset_sec=1800` (ثابت از ۲۰۲۶-۱۰-۲۱). `tsebot.clock.calibrate()` در پشتهٔ زنده هرگز صدا زده نمی‌شد.
   اگر ساعت ویندوز از آن روز اصلاح شده باشد، ربات ۳۰ دقیقه جلوتر است و «۰۸:۴۵:۰۱» در واقع ۰۸:۱۵ ارسال می‌شود (هماهنگ با درس ۱۴۰۵/۰۹/۰۱ «سفارش قبل از ۰۸:۴۵ رد شد»). **باید روی سرور تأیید شود** (پایین).
6. `order_candidates.py:513` «ثبت شد» را با regex روی پیام تشخیص می‌داد؛ سفارش‌هایی که flow.js در «سفارشات باز» تأیید کرده بود `submitted=false` ثبت شدند.

### رفع
- **`morning_orders.py` (جدید):** تنها مالک مرورگر در پیش‌گشایش. ساعت ۰۸:۴۱ شروع: کالیبره کردن ساعت ← بررسی/لاگین نشست ← لغو باقی‌ماندهٔ دیروز ← آماده‌سازی فروش‌های پیش‌گشایش (exit_engine) و خریدها (`order_candidates.prepare()`) ← اولین `flow.js` حدود ۴۰ ثانیه زودتر اجرا می‌شود، نماد را bind و فیلدها را پر می‌کند و با `FLOW_AT_EPOCH_MS` **دقیقاً در ۰۸:۴۵:۰۱.۰۰۰ تهران** کلیک می‌کند؛ بقیه پشت سر هم، هیچ چیز بعد از ۰۸:۵۹:۳۰. نتیجه و تأخیر کلیک (ms) در `state/morning_orders.json`.
- **`scheduler.py`:** هر کار در پس‌زمینه (Popen) اجرا می‌شود، حلقه هرگز منتظر نمی‌ماند، کار تکراری روی هم انباشته نمی‌شود، خروجی در `state/joblogs/<job>.log`. حذف `loginsync`، `pwreset`، `preclean`، `lotsell`، `preexit`، `order` و جایگزینی با `morning` (۰۸:۴۱). `ordsup`/`orderretry`/`ready45` فقط بعد از پایان `morning`. چک‌های مرورگری صبح (`ready30/40`, `preready`, `preflight`, …) بعد از ۰۸:۴۱ شروع نمی‌شوند. باگ نام تکراری `prices` (نسخهٔ ۲۱:۱۸ هرگز اجرا نمی‌شد) → `prices_night`.
- **ساعت:** `clock_sync.py` + کالیبره در شروع زمان‌بند و در `morning_orders`؛ منابع Date: `api.meibours.ir`، `online.meibours.ir`، `cdn.tsetmc.com`، سپس گوگل/کلودفلر؛ میانهٔ سه منبع.
- **`order_candidates.py`:** `prepare()` / `execute()` جدا، قفل روی کل اجرا، `submitted` از خروجی flow.js، جلوگیری از سفارش تکراری در همان روز، عدم ارسال سفارش با تعداد ۰ (flow.js در این حالت کل «آستانه حجم» را می‌خرید).
- **`run.py daemon`:** همهٔ مسیرهای معاملاتی تکراری پشت `config.json → daemon.trading` (پیش‌فرض `false`). دانلود/اسکن/ایمیل شبانه دست‌نخورده.
- **`config.json`:** `buy.preopen_first: "sell"` (طبق توضیح قبلی: فروش‌های معوق اول) — برای اولویت خرید `"buy"` بگذارید.

---

## باگ ۲ — لاگین نمی‌شد یا نشست می‌پرید

### علت‌ها
1. **هر اسکریپت یک کروم و یک لاگین جدید:** همهٔ `pw/*.js` با `launchPersistentContext` کروم تازه باز می‌کردند؛ کوکی نشست بین اجراها نمی‌ماند (`BROKER_KNOWLEDGE.md §1`) و کارگزار تک‌نشستی است ⇒ هر لاگین، نشست قبلی (و اپراتور) را بیرون می‌انداخت. در ساعات بازار ده‌ها اجرا در ساعت (`d_balance` هر ۵ دقیقه، `pnl` و `exitwatch` هر ۳ دقیقه، …).
2. **کشتن کروم:** `pw_lock.py:173,190` و `scheduler.py:171` (sweep بعد از هر کار) و `pw_reset` ساعت ۰۸:۴۴ همهٔ کروم‌های پروفایل را می‌کشتند.
3. **دزدیدن قفل:** `pw_lock.py:124` قفل بالای ۲۴۰ ثانیه را «کهنه» می‌دانست و از اجرای زنده (سفارش با OCR) می‌گرفت و کرومش را می‌کشت؛ گرفتن قفل هم اتمیک نبود.
4. **سقف لاگین:** `pw/session.js:57-58` فقط ۲ لاگین در روز و فقط ۰۷–۱۰ ⇒ `login_blocked` در `exits_log` (۱۴۰۵/۰۹/۰۲–۰۴).
5. `sync_login.py` ساعت ۰۸:۳۸ پایگاه کوکی ربات را بازنویسی و برای تأیید یک لاگین اضافه می‌کرد.

### رفع
- **یک کروم مشترک** روی پروفایل `state/pw-profile` با پورت اشکال‌زدایی `config.broker.debugger_address` (127.0.0.1:9333): `broker_browser.py`.
- **`pw/cdp_shared.js`** (خط اول همهٔ `pw/*.js`): به‌جای اجرای کروم جدید به همان کروم وصل می‌شود، همان تب واردشده را می‌دهد، `goto` روی تب واردشده انجام نمی‌شود، `close` فقط قطع اتصال است، `addCookies` قدیمی نادیده گرفته می‌شود، قفل بین‌پردازه‌ای `state/ui.lock` و اولویت صبح `state/ui_priority`. اگر کروم/CDP در دسترس نباشد به رفتار قبلی برمی‌گردد. کلید خاموش: `WARROBOT_SHARED_BROWSER=0`.
- **`session_keeper.py` + `pw/keepalive.js`:** هر دقیقه (۰۷:۵۰–۱۲:۴۵) با یک فراخوانی API درون صفحه نشست را گرم نگه می‌دارد و فقط وقتی واقعاً خارج شده باشد دوباره لاگین می‌کند. `state/session_health.json(l)`.
- **`session.js`:** به‌جای ممنوعیت، محدودکننده: حداقل ۹۰ ثانیه بین دو لاگین، حداکثر ۳۰ در روز؛ با فایل `state/OPERATOR_HOLD` لاگین خودکار متوقف می‌شود (وقتی خودتان دستی کار می‌کنید). `SESSION_FORCE_LOGIN` از اسکریپت‌های خروج حذف شد.
- **`pw_lock.py`:** اتمیک، فقط با مرگ صاحب یا بعد از ۱۵ دقیقه، بدون کشتن کروم. **`pw_reset.py`:** کروم مشترک زنده را نمی‌کشد (مگر `--force`). `sync_login.py` وقتی کروم مشترک روشن است کاری نمی‌کند.

---

## باگ ۳ — حلقهٔ فروش (حد سود/ضرر) اجرا نمی‌شد

### علت‌ها (شاهد: `history/exits_log.jsonl`)
1. `login_blocked` (باگ ۲) و `no_order_response`.
2. **قیمت غلط:** `intraday_exit.py:112` از `closingPriceDaily` ردیف `[-1]` را برمی‌داشت که **قدیمی‌ترین** ردیف است (API جدید→قدیم) ⇒ «حد ضرر −۳۶٪» آواپارس با ۴٬۳۷۰.
   `lot_exits.py:73` قیمت را از اسنپ‌شات کهنهٔ `pnl.json` می‌گرفت (آباد ۲٬۵۹۵ در حالی که ۲٬۰۹۴ معامله می‌شد) ⇒ خارج از دامنه ⇒ OrderId=0.
3. `pnl.py:166` متغیر محلی `broker_avg` دیکشنری را پوشانده بود ⇒ AttributeError برای پوزیشن با میانگین ۰ ⇒ مسیر جایگزین خراب اجرا می‌شد؛ و در خواندن خالی `return 0` به‌جای dict.
4. دو مسیر تصمیم در یک اجرا (`lot_exits.main` + مسیر pnl) ⇒ دو سفارش فروش هم‌زمان برای یک نماد (۲۱:۴۹:۲۰).
5. حد ضرر با «آخرین قیمت» در بازار نزولی هرگز پر نمی‌شود؛ `distbot.sell_loop` (`run.py:465`) با `b = None` (`distbot.py:462`) هر دقیقه خطا می‌داد.

### رفع — `exit_engine.py` (یک مسیر)
- هر لات ژورنال با هزینهٔ کارمزددار خودش؛ سهام بدون لات با میانگین کارگزار؛ لات امروز قضاوت نمی‌شود؛ افران/خساپا هرگز.
- قیمت زنده: قیمت پنل کارگزار (اسنپ‌شات تازه ≤۸ دقیقه) با تطبیق «آخرین معامله» TSETMC (`price_discovery.last_trade`)؛ اختلاف >۱۰٪ یا نبود قیمت ⇒ رد، نه حدس.
- قیمت سفارش: حد سود ⇒ بهترین خرید (وگرنه آخرین)، حد ضرر ⇒ بهترین خرید وگرنه **کف دامنه**؛ همیشه داخل دامنهٔ مجاز روز.
- بدون تکرار: تعداد = سهم سررسیده − سفارش فروش باز − ارسال‌های ۱۰ دقیقهٔ اخیر؛ اگر سفارش‌های باز خوانده نشود ⇒ ارسال نمی‌شود. سفارش حد ضرر **خود ربات** اگر ≥۱۰ دقیقه بالای قیمت قابل‌اجرا بماند لغو و با قیمت جدید ثبت می‌شود (`sell.reprice_after_min`)؛ سفارش‌های دستی شما دست نمی‌خورند.
- `intraday_exit.py` (هر ۳ دقیقه ۰۹:۰۰–۱۲:۳۰) و `morning_orders.py` (فروش پیش‌گشایش در ۰۸:۴۵:۰۱) هر دو از همین موتور استفاده می‌کنند. ثبت در `state/exits_log.jsonl` و `state/exit_orders.json`، ژورنال و انتقال سود به افران مثل قبل.

---

## امنیت
`tese-robot/config/email.json` → `app_password: ""`؛ رمز از `WARROBOT_SMTP_PASSWORD` خوانده می‌شود (`tsebot/alerts.py`). الگو به `.gitignore` و راهنما به README اضافه شد.
**رمز قبلی در تاریخچهٔ git هست: آن App Password را در Google باطل کنید.**

---

## استقرار روی سرور ویندوز
```powershell
cd "C:\Users\Administrator\Desktop\DeepSeek DeskTop"   # مسیر کپی‌های WarRobot و TeseRobot
# ۱) فایل‌های شاخه را روی WarRobot\ و TeseRobot\ کپی/merge کنید (state\ دست نخورد)
# ۲) رمز ایمیل
setx WARROBOT_SMTP_PASSWORD "<new app password>" /M
# ۳) هر کروم باز روی pw-profile را ببندید، سپس کروم مشترک:
cd WarRobot
C:\TSE_Strategy\venv\Scripts\python.exe broker_browser.py          # {"ok": true, ...}
C:\TSE_Strategy\venv\Scripts\python.exe session_keeper.py --once   # "state": "alive" یا "relogged"
# ۴) آزمون‌ها
C:\TSE_Strategy\venv\Scripts\python.exe -m unittest tests.test_hark_fixes -v
node tests\js\test_cdp_shared.js
# ۵) ری‌استارت با env جدید
pm2 restart warsched --update-env ; pm2 restart warrobot --update-env ; pm2 save
```
**تمرین زمان‌بندی بدون سفارش** (خارج از ساعت بازار، روی کروم مشترک):
```powershell
$env:FLOW_DRY="1"; python morning_orders.py --force --at 14:30:01
# state\morning_orders.json → results[0].click_lag_ms باید نزدیک 0 باشد
python intraday_exit.py --dry --force      # تصمیم‌ها بدون سفارش، state\exit_state.json
```
(اختیاری) نگهبان جدا: `pm2 start session_keeper.py --name warsession --interpreter C:\TSE_Strategy\venv\Scripts\python.exe -- --loop 45`

## آنچه باید روی سرور تأیید کنید
1. **ساعت:** `python clock_sync.py` را اجرا کنید؛ `measured` باید با واقعیت بخواند. اگر ساعت ویندوز درست است، offset باید ~۰ شود (نه ۱۸۰۰). ساعت سرور را با `w32tm /resync` هم هماهنگ کنید.
2. کروم با پورت ۹۳۳۳ روی `state\pw-profile` بالا بیاید و `session_keeper.py --once` حالت `alive` بدهد؛ در `state\session_events.jsonl` باید `attached_shared` ببینید و تعداد `login` در روز ۱–۲ باشد.
3. فردا صبح: `state\morning_orders.json` (`click_lag_ms`، `submitted`) و `orders_log.jsonl`؛ زمان سفارش در پنل کارگزار باید ۰۸:۴۵:۰۱ باشد.
4. بعد از ۰۹:۰۳: `state\exit_state.json` و `exits_log.jsonl` — قیمت‌ها داخل دامنه و بدون `login_blocked`.
5. اگر سفارش حد ضرر ساعت ۰۸:۴۵ در صف کف بماند و بخواهید خرید اول باشد: `buy.preopen_first = "buy"`.
6. ورود دستی خودتان نشست ربات را می‌پراند (کارگزار تک‌نشستی است)؛ هنگام کار دستی فایل خالی `state\OPERATOR_HOLD` بسازید و بعد پاک کنید.
7. فیلدهای `ClosingPrice/GetClosingPriceInfo` (pDrCotVal) از سرور ایران در دسترس باشد؛ در غیر این صورت فقط قیمت پنل استفاده می‌شود.
