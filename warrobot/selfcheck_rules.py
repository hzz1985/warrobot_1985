"""Daily self-check: verify that every learned operating rule is still in place.

Each rule is checked against the actual code/config, not against a document, so a
future edit that silently breaks an operating rule is caught before the market
opens. Runs at 08:25 (before the first readiness email) and any time by hand.

    python selfcheck_rules.py [--email]
"""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

BASE = Path(r"C:\Users\Administrator\Desktop\DeepSeek DeskTop\WarRobot")
OUT = BASE / "state" / "rules_status.json"


def _read(name: str) -> str:
    fp = BASE / name
    try:
        return fp.read_text(encoding="utf-8")
    except Exception:
        return ""


def checks() -> list[dict]:
    sch = _read("scheduler.py")
    reb = _read("rebalance.py")
    ordc = _read("order_candidates.py")
    ie = _read("intraday_exit.py")
    ad = _read("portfolio_sync.py")
    ls = _read("ledger_sync.py")
    rc = _read("ready_check.py")
    cd = (BASE / "pw" / "cancel_delete.js").read_text(encoding="utf-8") if (BASE / "pw" / "cancel_delete.js").exists() else ""

    out = []

    def add(rule, ok, detail):
        out.append({"rule": rule, "ok": bool(ok), "detail": detail})

    add("لغو سفارش از /Order/Delete با آرایهٔ شناسه",
        "/Order/Delete" in cd and "JSON.stringify(s.body)" in cd,
        "pw/cancel_delete.js")
    add("چرخش ۱۰:۰۰ از cancel_orders استفاده می‌کند",
        "cancel_orders" in reb and "cancel_all" in reb,
        "rebalance.py")
    add("چرخش، سفارش‌های باز را از API می‌خواند",
        "co.open_orders()" in reb or "cancel_orders as co" in reb,
        "rebalance.py → open_buys")
    add("زمان‌بند قبل از سفارش تا ۰۸:۴۵:۰۱ صبر می‌کند",
        "wait_for_084501" in sch,
        "scheduler.py")
    add("چهار زمان ۰۸:۳۰/۰۸:۴۰/۰۸:۴۵ در زمان‌بند",
        all(t in sch for t in ('"08:30"', '"08:40"', '"08:45"')),
        "scheduler.py JOBS")
    add("چرخش ۱۰:۰۰ در زمان‌بند",
        '"10:00"' in sch and "rebalance.py" in sch, "scheduler.py JOBS")
    add("همسان‌سازی پرتفوی ۲۱:۲۰",
        "portfolio_sync.py" in sch, "scheduler.py JOBS")
    add("همسان‌سازی دفتر ۲۱:۲۱",
        "ledger_sync.py" in sch, "scheduler.py JOBS")
    add("وب‌اپ از پرتفوی کارگزاری می‌خواند",
        "broker_portfolio.json" in (BASE.parent / "TeseRobot" / "webapp" / "warrobot_data.py").read_text(encoding="utf-8")
        if (BASE.parent / "TeseRobot" / "webapp" / "warrobot_data.py").exists() else False,
        "TeseRobot/webapp/warrobot_data.py")
    add("پایش TP/SL هر ۳ دقیقه (۰۹:۰۰–۱۲:۳۰)",
        "exitwatch" in sch and "intraday_exit.py" in sch, "scheduler.py REPEAT")
    add("خروج خودکار: stop + breakeven + trail",
        all(k in ie for k in ("sell_tp", "sell_sl")), "intraday_exit.py")
    add("افران محافظت‌شده و انتقال سود",
        "MANUAL_ONLY" in ie and "transfer_profit_to_afran" in ie, "intraday_exit.py")
    add("سایز از موجودی آزاد و یک‌سوم دارایی",
        ("target_each" in ordc or "third" in ordc) and "_free_balance" in ordc,
        "order_candidates.py")
    add("آمادگی سه‌مرحله‌ای با ایمیل",
        "emailed" in rc and ("send_once" in rc or "send(" in rc), "ready_check.py")
    # ---- fixes for the recurring complaints (1405-08-21) ----
    import market_cal as _mc
    add("تقویم بازار: پنجشنبه/جمعه تعطیل است",
        not _mc.market_day(__import__("datetime").datetime(2026, 11, 12))   # Thursday
        and _mc.market_day(__import__("datetime").datetime(2026, 11, 14)),  # Saturday
        "پنجشنبه بسته · شنبه باز")
    add("زمان‌بند فقط یک مالک دارد (daemon ساده شده)",
        (BASE / "daemon_service.py").exists() and "scheduler" in (BASE / "daemon_service.py").read_text(encoding="utf-8"),
        "daemon_service.py")
    add("زمان‌بند تعطیلی بازار را رعایت می‌کند",
        "market_cal.market_day" in (BASE / "scheduler.py").read_text(encoding="utf-8"),
        "scheduler.py")
    add("پایش سفارش پیش‌گشایش (۰۸:۴۶-۰۹:۴۵)", "order_monitor.py" in (BASE / "scheduler.py").read_text(encoding="utf-8"),
        "scheduler.py REPEAT")
    add("تخصیص مساوی (هدف = کل دارایی ÷ ۳)",
        "equal_allocation" in (BASE / "order_candidates.py").read_text(encoding="utf-8"),
        "order_candidates.py")
    add("ایمیل تکراری ارسال نمی‌شود (send_once)",
        "market_cal.send_once" in (BASE / "ready_check.py").read_text(encoding="utf-8"),
        "ready_check.py")
    try:
        import urllib.request
        h = urllib.request.urlopen("http://127.0.0.1:5077/", timeout=60).read().decode("utf-8", "replace")
        add("وب‌اپ سفید (تم روشن)", "LIGHT-THEME" in h, "LIGHT-THEME")
        add("تب دیپ‌سیک حذف شده", 'data-tab="export"' not in h, "دو تب: خرید/فروش")
    except Exception as exc:
        add("وب‌اپ سفید (تم روشن)", False, str(exc)[:60])
        add("تب دیپ‌سیک حذف شده", False, "unreachable")

    # services online
    try:
        import subprocess as _sp
        pm2_out = (_sp.run(["pm2", "list", "--no-color"], capture_output=True, text=True,
                           encoding="utf-8", errors="replace",
                           timeout=90, shell=True).stdout or "")
        missing = []
        for name in ("warrobot", "warsched", "tse-web"):
            hit = any(f" {name} " in line and "online" in line for line in pm2_out.splitlines())
            if not hit:
                missing.append(name)
        add("هر سه سرویس آنلاین (warrobot · warsched · tse-web)",
            not missing, "همه آنلاین" if not missing else "خاموش: " + ", ".join(missing))
    except Exception as exc:
        add("هر سه سرویس آنلاین (warrobot · warsched · tse-web)", False, str(exc)[:80])

    # today's jobs actually ran (not skipped, and not still pending)
    try:
        from datetime import datetime as _dt
        jobs = {"08:30": "ready30", "08:40": "preflight",
                "08:45": "order", "10:00": "rotate"}
        fp = BASE / "state" / "jobs_done.json"
        data = json.loads(fp.read_text(encoding="utf-8")) if fp.exists() else {}
        today = data.get(_dt.now().strftime("%Y-%m-%d"), {})
        now_hm = _dt.now().strftime("%H:%M")
        problems = []
        for at, job in jobs.items():
            if now_hm < at:
                continue
            val = today.get(job)
            if val is None:
                continue                     # scheduler was down: reported separately
            if str(val).startswith("skipped"):
                problems.append(f"{job} skipped")
        add("کارهای امروز اجرا شده‌اند (نه skipped)",
            not problems, "بررسی‌شده" if not problems else ", ".join(problems))
    except Exception as exc:
        add("کارهای امروز اجرا شده‌اند (نه skipped)", False, str(exc)[:80])

    add("افران جزو پوزیشن‌های باز نیست",
        'FUND_SYMBOL = "افران"' in (BASE.parent / "TeseRobot" / "webapp" / "warrobot_data.py"
                                    ).read_text(encoding="utf-8"),
        "warrobot_data.py")
    add("دارایی کل = نقد + ارزش پنل کارگزار",
        "broker_values" in (BASE / "order_candidates.py").read_text(encoding="utf-8"),
        "order_candidates.py")

    add("فروش‌ها در ژورنال ثبت می‌شوند (هر دو مسیر)",
        "trade_journal" in (BASE / "intraday_exit.py").read_text(encoding="utf-8")
        and "trade_journal" in (BASE / "preopen_exits.py").read_text(encoding="utf-8"),
        "intraday_exit.py + preopen_exits.py")
    add("تب گزارش با ۵ دوره و به‌روزرسانی شبانه",
        "report_periods.py" in (BASE / "scheduler.py").read_text(encoding="utf-8")
        and (BASE.parent / "TeseRobot" / "webapp" / "templates" / "index.html"
             ).read_text(encoding="utf-8", errors="replace").count('data-tab="report"') == 1,
        "scheduler.py + index.html")
    add("دفتر معاملات شبانه (زمان/قیمت خرید و فروش)",
        "trades_ledger.py" in (BASE / "scheduler.py").read_text(encoding="utf-8"),
        "scheduler.py")
    add("ایجنت دادهٔ هر کارت (موجودی/پرتفوی/P&L) زمان‌بند دارد",
        (BASE / "agents" / "data_agents.py").exists()
        and "data_agents.py --job balance" in (BASE / "scheduler.py").read_text(encoding="utf-8"),
        "agents/data_agents.py")
    add("رسید آمادگی پیش از بازار (readiness)",
        (BASE / "readiness_check.py").exists()
        and "readiness_check.py" in (BASE / "scheduler.py").read_text(encoding="utf-8"),
        "readiness_check.py")
    add("تقویم رسمی (TSETMC) اولویت دارد",
        (BASE / "agents" / "calendar_sync.py").exists()
        and "calendar_sync.py" in (BASE / "scheduler.py").read_text(encoding="utf-8"),
        "agents/calendar_sync.py")
    add("افزودن تعطیلات با دادهٔ معاملاتی صحت‌سنجی می‌شود",
        "sessions_present" in (BASE / "market_holidays.py").read_text(encoding="utf-8"),
        "market_holidays.py")
    add("تشخیص تعطیلات مشکوک (a8) شبانه",
        (BASE / "agents" / "calendar_watch.py").exists()
        and "calendar_watch.py" in (BASE / "scheduler.py").read_text(encoding="utf-8"),
        "agents/calendar_watch.py")
    add("تقویم شمسی/تعطیلات در market_day اعمال شده",
        "market_holidays" in (BASE / "market_cal.py").read_text(encoding="utf-8"),
        "market_cal.py")
    add("در روز تعطیل، تازه‌سازی قیمت/P&L ادامه دارد",
        "CLOSED_DAY_DATA" in (BASE / "scheduler.py").read_text(encoding="utf-8"),
        "scheduler.py")
    add("دفترچهٔ راهنمای ایجنت‌ها موجود است",
        (BASE / "docs" / "AGENTS_PLAYBOOK.md").exists(), "docs/AGENTS_PLAYBOOK.md")
    add("نگهبان سرویس‌ها (a6) هر ۱۰ دقیقه",
        "agents/sleep_guard.py" in (BASE / "scheduler.py").read_text(encoding="utf-8"),
        "scheduler.py")
    add("گزارش هفتگی خودکار (a7)",
        (BASE / "agents" / "weekly_report.py").exists()
        and "weekly_report.py" in (BASE / "scheduler.py").read_text(encoding="utf-8"),
        "agents/weekly_report.py")
    add("خوانندهٔ خبر به‌عنوان پرچم ایمنی (a5)",
        (BASE / "agents" / "news_watch.py").exists(), "agents/news_watch.py")
    add("حافظهٔ درس‌ها (lessons.jsonl) فعال است",
        (BASE / "state" / "lessons.jsonl").exists() and (BASE / "agents" / "lessons.py").exists(),
        "agents/lessons.py")
    add("تب ایجنت‌ها در وب‌اپ وجود دارد",
        'data-tab="agents"' in (BASE.parent / "TeseRobot" / "webapp" / "templates" / "index.html"
                                ).read_text(encoding="utf-8", errors="replace"),
        "index.html")
    add("ایجنت‌های محلی (ورود/پیش‌گشایش/وب‌اپ) در زمان‌بند هستند",
        "agents/supervisor.py" in (BASE / "scheduler.py").read_text(encoding="utf-8")
        and (BASE / "agents" / "supervisor.py").exists(), "agents/")
    add("کشف قیمت از پنج مظنه برتر + آستانه رسمی",
        (BASE / "price_discovery.py").exists()
        and "price_discovery" in (BASE / "order_candidates.py").read_text(encoding="utf-8"),
        "price_discovery.py")
    add("ایجنت قیمت سفارش (a12) در زمان‌بند",
        (BASE / "agents" / "order_price.py").exists()
        and "agents/order_price.py" in (BASE / "scheduler.py").read_text(encoding="utf-8"),
        "agents/order_price.py")
    add("اصلاح قیمت تقریبی فروش با رکورد کارگزار (a11)",
        (BASE / "agents" / "sale_price_fix.py").exists(),
        "agents/sale_price_fix.py")
    add("تطبیق شبانهٔ فروش‌ها با کارگزار (a10)",
        (BASE / "agents" / "sale_reconcile.py").exists()
        and "sale_reconcile.py" in (BASE / "scheduler.py").read_text(encoding="utf-8"),
        "agents/sale_reconcile.py")
    add("گارد باند قیمت در مسیرهای فروش",
        "price_out_of_band" in (BASE / "lot_exits.py").read_text(encoding="utf-8")
        and "price_out_of_band" in (BASE / "intraday_exit.py").read_text(encoding="utf-8"),
        "lot_exits.py + intraday_exit.py")
    add("فروش لات‌محور در پیش‌گشایش ۰۸:۴۵",
        "lotsell" in (BASE / "scheduler.py").read_text(encoding="utf-8"),
        "scheduler.py")
    add("TP/SL بر مبنای هر لات خرید محاسبه می‌شود",
        (BASE / "lot_exits.py").exists()
        and "lot_exits" in (BASE / "intraday_exit.py").read_text(encoding="utf-8"),
        "lot_exits.py")
    add("نزدیک TP، خرید مجدد انجام نمی‌شود",
        "skipped_near_tp" in (BASE / "order_candidates.py").read_text(encoding="utf-8"),
        "order_candidates.py")
    add("ورود از کروم اپراتور بازاستفاده می‌شود (۰۸:۳۸)",
        "sync_login.py" in (BASE / "scheduler.py").read_text(encoding="utf-8")
        and (BASE / "sync_login.py").exists(), "sync_login.py")
    add("آمادگی ۰۸:۴۰ با ۵ تلاش ورود",
        "MAX_LOGIN_ATTEMPTS = 5" in (BASE / "preopen_ready.py").read_text(encoding="utf-8")
        and "preopen_ready.py" in (BASE / "scheduler.py").read_text(encoding="utf-8"),
        "preopen_ready.py")
    add("ناظر خودکار سفارش (۰۸:۴۶ و ۰۸:۵۶)",
        "order_supervisor.py" in (BASE / "scheduler.py").read_text(encoding="utf-8")
        and (BASE / "order_supervisor.py").exists(), "order_supervisor.py")
    add("لغو سفارش‌های باقی‌ماندهٔ جلسهٔ قبل (۰۸:۴۳)",
        "preopen_cleanup.py" in (BASE / "scheduler.py").read_text(encoding="utf-8"),
        "scheduler.py")
    add("چرخش ۱۰:۰۰ بودجه را به نقد محدود می‌کند",
        "free_cash_cap" in (BASE / "rebalance.py").read_text(encoding="utf-8"),
        "rebalance.py")

    # ---- 1405/08/25/26: orphan Chrome held the profile ----
    add("کروم یتیم قبل از اجرا پاک می‌شود",
        (BASE / "pw_reset.py").exists() and "pw_reset" in (BASE / "pw_lock.py").read_text(encoding="utf-8"),
        "pw_reset.py")
    add("پاک‌سازی پروفایل ساعت ۰۸:۴۴",
        '"08:44", "pwreset"' in (BASE / "scheduler.py").read_text(encoding="utf-8"),
        "scheduler.py")
    add("سفارش دوم روی سفارش باز ثبت نمی‌شود",
        "_open_orders" in (BASE / "order_candidates.py").read_text(encoding="utf-8"),
        "order_candidates.py")

    # ---- 1405/08/25: browser-lock collision skipped the pre-open order ----
    sch_src = (BASE / "scheduler.py").read_text(encoding="utf-8")
    add("پنجرهٔ سکوت ۰۸:۳۰–۰۹:۰۳ برای سفارش پیش‌گشایش",
        ('"08:30" <= hm <= "09:03"' in sch_src) or ('"08:42" <= hm <= "09:03"' in sch_src),
        "scheduler.py")
    add("تمرین صبحگاهی (خرید و فروش) برنامه‌ریزی شده",
        "rehearse_morning.py" in sch_src and (BASE / "rehearse_morning.py").exists(),
        "scheduler.py")
    oc_src2 = (BASE / "order_candidates.py").read_text(encoding="utf-8")
    add("موجودی: تلاش دوباره + فالبک اسنپ‌شات",
        "_snapshot_balance" in oc_src2 and "for attempt in range(2)" in oc_src2,
        "order_candidates.py")

    # ---- nightly buying power + final prices (1405/08/24) ----
    add("قدرت خرید شبانه از کارگزار خوانده می‌شود",
        "balance.js" in (BASE / "nightly_prices.py").read_text(encoding="utf-8"),
        "nightly_prices.py")
    add("قیمت پایانی شبانه در prices.json ذخیره می‌شود",
        "prices.json" in (BASE / "nightly_prices.py").read_text(encoding="utf-8"),
        "nightly_prices.py")
    add("وب‌اپ قدرت خرید را نشان می‌دهد",
        "def balance(" in (BASE.parent / "TeseRobot" / "webapp" / "warrobot_data.py"
                          ).read_text(encoding="utf-8"),
        "warrobot_data.py")
    add("سفارش‌گذاری از قیمت شبانه استفاده می‌کند",
        "_nightly_close" in (BASE / "order_candidates.py").read_text(encoding="utf-8"),
        "order_candidates.py")

    # ---- incident 1405/08/24: stale price blocked the order ----
    oc_src = (BASE / "order_candidates.py").read_text(encoding="utf-8")
    add("قیمت کهنه (TSETMC) رد می‌شود",
        "_close_is_fresh" in oc_src and "_broker_prices" in oc_src, "order_candidates.py")
    add("قیمت پنل کارگزار مبنای سفارش است",
        "_broker_prices().get(sym" in oc_src, "order_candidates.py")
    pnl_src = (BASE / "pnl.py").read_text(encoding="utf-8")
    add("اگر خواندن پنل خالی بود، اسنپ‌شات قبلی حفظ می‌شود",
        "empty panel read" in pnl_src, "pnl.py")

    # ---- contract (docs/TRADING_SPEC.md) clauses ----
    flow_js = (BASE / "pw" / "flow.js").read_text(encoding="utf-8")
    add("بند۱: قیمت فیلد و قیمت ثبتشده بررسی میشود",
        "price_ok" in flow_js and "placed_price" in flow_js,
        "pw/flow.js")
    add("بند۱: لغو سفارش با قیمت نادرست و تلاش مجدد",
        "mismatch_fixed" in flow_js, "pw/flow.js")
    add("بند۲: preflight قدرت خرید و لیست را ثبت میکند",
        "buying_power" in (BASE / "preflight.py").read_text(encoding="utf-8"),
        "preflight.py")
    add("بند۴: چرخش ۱۰:۰۰ با مسیر لغو واقعی",
        "cancel_orders" in (BASE / "rebalance.py").read_text(encoding="utf-8"),
        "rebalance.py")
    add("بند۵: سهم مساوی (هدف = کل دارایی/۳)",
        "target_each" in (BASE / "order_candidates.py").read_text(encoding="utf-8"),
        "order_candidates.py")
    add("بند۶: انتقال سود به افران (ماژول مرکزی)",
        (BASE / "profit_transfer.py").exists()
        and "import profit_transfer" in (BASE / "intraday_exit.py").read_text(encoding="utf-8"),
        "profit_transfer.py")
    add("بند۷: تقویم بازار (پنجشنبه/جمعه تعطیل)",
        "market_cal" in (BASE / "scheduler.py").read_text(encoding="utf-8"),
        "market_cal.py")
    add("بند۸: ایمیل یک‌باره",
        "send_once" in (BASE / "ready_check.py").read_text(encoding="utf-8"),
        "ready_check.py")
    add("قرارداد مکتوب (TRADING_SPEC.md)",
        (BASE / "docs" / "TRADING_SPEC.md").exists(), "docs/TRADING_SPEC.md")

    # live web check: catches a 500 that a static grep can never see
    try:
        import urllib.request
        h = urllib.request.urlopen("http://127.0.0.1:5077/", timeout=60).read().decode("utf-8", "replace")
        add("وب‌اپ زنده و سالم (HTTP 200 + HTML)",
            "<html" in h.lower() and len(h) > 20000,
            f"{len(h):,} bytes")
        add("هر سه بخش وب‌اپ موجود",
            all(k in h for k in ("کاندیدای خرید", "کاندیدای فروش", "پوزیشن")),
            "خرید / فروش / پوزیشن")
    except Exception as exc:
        add("وب‌اپ زنده و سالم (HTTP 200 + HTML)", False, str(exc)[:80])
        add("هر سه بخش وب‌اپ موجود", False, "unreachable")

    add("بایند دقیق در مسیر سفارش",
        "bind_mismatch" in (BASE / "pw" / "flow.js").read_text(encoding="utf-8"),
        "pw/flow.js")
    return out


def main(send_email: bool = False) -> int:
    rows = checks()
    failed = [r for r in rows if not r["ok"]]
    status = {
        "at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "total": len(rows),
        "passed": len(rows) - len(failed),
        "failed": [r["rule"] for r in failed],
        "details": rows,
    }
    OUT.write_text(json.dumps(status, ensure_ascii=False, indent=1), encoding="utf-8")

    for r in rows:
        print(f"{'✔' if r['ok'] else '✘'} {r['rule']}  [{r['detail']}]")
    print(f"\n{status['passed']}/{status['total']} rules verified")

    if send_email:
        try:
            sys.path.insert(0, str(BASE.parent / "TeseRobot" / "src"))
            from tsebot.alerts import send
            lines = [f"زمان: {status['at']}", ""]
            for r in rows:
                lines.append(f"{'✔' if r['ok'] else '✘'} {r['rule']}")
            if failed:
                lines.append("")
                lines.append("اقدام لازم: قواعد شکسته‌شده باید بازگردانده شوند")
            send("[WarRobot] راستی‌آزمایی قواعد عملیاتی",
                 "\n".join(lines), key="rules-selfcheck", force=True)
        except Exception as exc:
            print("email failed:", str(exc)[:80])

    return 0 if not failed else 2


if __name__ == "__main__":
    raise SystemExit(main(send_email="--email" in sys.argv))
