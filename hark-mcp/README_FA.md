# hark-mcp — اتصال Hark به سرور ویندوز WarRobot

این پوشه یک «سرور MCP» است که روی همان ویندوز سرورِ ربات اجرا می‌شود و به Hark اجازه می‌دهد
سرور را مدیریت کند: اجرای دستور PowerShell/CMD، خواندن و نوشتن فایل، دیدن لاگ‌ها، pm2، git،
اطلاعات سیستم و گرفتن عکس از صفحه (مثلاً دیدن Chrome کارگزاری).

سرور فقط روی `127.0.0.1:8765` گوش می‌دهد (از بیرون مستقیم قابل دسترس نیست) و از طریق
**Cloudflare Tunnel** که یک اتصال «خروجی» است به اینترنت وصل می‌شود؛ پس نیازی به باز کردن پورت،
IP ثابت یا تنظیم فایروال/NAT نیست (برای سرورهای داخل ایران مناسب‌تر است).

---

## ۱) پیش‌نیازها
- Python نسخه 3.10 یا بالاتر (همان پایتونی که ربات استفاده می‌کند کافی است).
- Node.js و pm2 (روی سرور ربات از قبل نصب است).
- دسترسی اینترنت برای نصب پکیج‌ها (pip) و دانلود cloudflared.

## ۲) نصب (یک بار)
1. PowerShell را **Run as Administrator** باز کنید.
2. به پوشهٔ مخزن بروید (جایی که پوشهٔ `hark-mcp` هست)، مثلاً:
   ```powershell
   cd "C:\Users\Administrator\Desktop\DeepSeek DeskTop\WarRobot"
   ```
3. نصب را اجرا کنید:
   ```powershell
   powershell -ExecutionPolicy Bypass -File .\hark-mcp\install.ps1
   ```
   اگر پوشهٔ ربات جای دیگری است: `-BotDir "D:\...\WarRobot"` را اضافه کنید.

این اسکریپت:
- یک محیط مجازی پایتون (`hark-mcp\.venv`) می‌سازد و پکیج‌ها را نصب می‌کند،
- یک **توکن تصادفی ۴۸ کاراکتری** می‌سازد و **فقط یک بار** با رنگ زرد چاپ می‌کند،
- توکن را در متغیر محیطی `HARK_MCP_TOKEN` ذخیره می‌کند،
- سرور را با نام `hark-mcp` در pm2 ثبت و `pm2 save` می‌کند.

> ⚠️ توکن را همان لحظه کپی کنید. اگر گم شد: `install.ps1 -NewToken` را دوباره اجرا کنید
> (توکن قبلی باطل می‌شود و باید توکن جدید را دوباره در Hark وارد کنید).

بررسی:
```powershell
pm2 status
pm2 logs hark-mcp --nostream --lines 30
```
باید خط `hark-mcp listening on http://127.0.0.1:8765/mcp` را ببینید.

## ۳) روشن کردن تونل (گرفتن آدرس اینترنتی)
### روش سریع (بدون دامنه)
```powershell
powershell -ExecutionPolicy Bypass -File .\hark-mcp\start_tunnel.ps1
```
در پایان آدرسی شبیه این چاپ می‌شود:
```
MCP URL to give Hark (in chat):  https://xxxx-yyyy.trycloudflare.com/mcp
```
نکته: این آدرس با هر بار ری‌استارت تونل یا سرور **عوض می‌شود** و باید آدرس جدید را به Hark بدهید.

دستی هم می‌شود اجرا کرد:
```powershell
cloudflared tunnel --url http://127.0.0.1:8765
```

### روش پایدار (دامنهٔ خودتان روی Cloudflare)
اگر دامنه‌ای دارید که DNS آن روی Cloudflare است:
```powershell
powershell -ExecutionPolicy Bypass -File .\hark-mcp\start_tunnel.ps1 -Named -Hostname mcp.yourdomain.com
```
بار اول مرورگر باز می‌شود تا وارد Cloudflare شوید و دامنه را تأیید کنید. آدرس ثابت می‌شود:
`https://mcp.yourdomain.com/mcp`

### اگر Cloudflare از داخل شبکه در دسترس نبود
- `-Protocol quic` یا `-Protocol http2` را امتحان کنید (پیش‌فرض http2 است که روی پورت 443 TCP کار می‌کند).
- جایگزین: **ngrok** — از ngrok.com ثبت‌نام کنید، authtoken را تنظیم کنید و:
  ```powershell
  ngrok config add-authtoken <AUTHTOKEN>
  ngrok http 8765
  ```
  آدرس `https://....ngrok-free.app` را بردارید و `/mcp` را به انتهایش اضافه کنید.

## ۴) چه چیزی به Hark بدهید
| چه چیزی | کجا |
|---|---|
| آدرس، مثلاً `https://xxxx.trycloudflare.com/mcp` | در **چت** بفرستید |
| توکن ۴۸ کاراکتری | **فقط در فرم امن Hark** (Secure form) — هرگز در چت ننویسید |

اگر توکن را اشتباهی در چت گذاشتید: فوراً `install.ps1 -NewToken` بزنید و توکن جدید را در فرم امن وارد کنید.

تست سریع: باز کردن آدرس در مرورگر باید خطای `{"error":"unauthorized"}` (کد 401) بدهد؛ یعنی سرور بالا است و بدون توکن کسی وارد نمی‌شود.

## ۵) ایمنی
- **همهٔ** فراخوانی‌ها در `hark-mcp\audit.jsonl` ثبت می‌شوند (زمان، ابزار، آرگومان‌ها با حذف رمزها، کد خروج، مدت).
- **کلید قطع معامله (Kill-switch):** تا وقتی `HARK_MCP_LIVE_TRADING` برابر `0` است (پیش‌فرض)، اجرای
  اسکریپت‌هایی که سفارش واقعی می‌فرستند یا لغو می‌کنند (order_candidates.py، morning_orders.py بدون --dry،
  lot_exits.py --live، intraday_exit.py، preopen_exits.py، rebalance.py، order_retry.py، retry_orders.py،
  order_supervisor.py، cancel_open_orders.py، cancel_orders.py، pw/flow.js، pw/api_order.js، pw/cancel_*.js،
  `broker_pw.py order`) از طریق Hark **رد می‌شود**؛ مگر `FLOW_DRY=1` در همان دستور باشد (اجرای آزمایشی).
  ری‌استارت/استارت دیمن‌های ربات با pm2 مجاز است. فهرست الگوها در `hark-mcp\guard.json` است و قابل ویرایش.
  این یک «کمربند ایمنی» در برابر اشتباه است، نه دیوار امنیتی (کسی که توکن دارد دسترسی کامل شِل دارد).
- برای اجازهٔ معاملهٔ واقعی از طریق Hark:
  ```powershell
  [Environment]::SetEnvironmentVariable("HARK_MCP_LIVE_TRADING","1","User")
  $env:HARK_MCP_LIVE_TRADING="1"; pm2 restart hark-mcp --update-env; pm2 save
  ```
- **حالت فقط‌خواندنی:** با `HARK_MCP_READONLY=1` فقط خواندن فایل/لاگ، وضعیت pm2 و git مجاز است.
- قبل از حذف یا بازنویسی فایل، نسخهٔ پشتیبان در `hark-mcp\backups\` ذخیره می‌شود.
- توکن در متغیر محیطی و در فایل dump خودِ pm2 ذخیره می‌شود؛ از سرور محافظت کنید.

## ۶) دستورهای مفید
```powershell
pm2 restart hark-mcp          # ری‌استارت سرور
pm2 stop hark-tunnel          # قطع فوری دسترسی Hark از اینترنت
pm2 start hark-tunnel         # وصل دوباره (در روش سریع آدرس عوض می‌شود)
pm2 logs hark-tunnel --nostream --lines 50
```
**قطع کامل دسترسی:** `pm2 stop hark-tunnel` یا `pm2 stop hark-mcp`.

## ۷) عکس صفحه
ابزار `screenshot_desktop` فقط وقتی کار می‌کند که pm2 در **جلسهٔ کاربری لاگین‌شده** (همان RDP/Desktop
که Chrome در آن باز است) اجرا شده باشد، نه به‌صورت Windows Service. اگر RDP را ببندید (Disconnect)
ممکن است عکس سیاه شود.

## ابزارهای موجود (برای مرجع)
`run_powershell`، `run_cmd` (با `background=true` → `job_status`، `job_output`، `job_kill`)،
`read_file`، `write_file`، `list_dir`، `delete_path`، `search_files`، `upload_file`، `download_file`،
`git`، `pm2`، `tail_log`، `system_info`، `screenshot_desktop`.

## تست (برای توسعه‌دهنده)
```bash
pip install -r hark-mcp/requirements.txt pytest httpx
pytest -q hark-mcp/tests
```
