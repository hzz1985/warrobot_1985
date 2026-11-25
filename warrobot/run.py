"""TseBot_Dist runner — scan / live / buy / sell / daemon.

    python run.py scan            # nightly: symbols inside the 0.25% band
    python run.py live            # 09:00: confirm on the live book + email
    python run.py buy [--dry]     # 09:01: buy the confirmed list
    python run.py sell [--dry]    # daily sell loop
    python run.py daemon          # full schedule
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "src"))

import distbot as bot  # noqa: E402
from tsebot.alerts import send  # noqa: E402

CAND = HERE / "state" / "candidates.csv"
LIVE = HERE / "state" / "live_candidates.csv"


def now_tehran_date():
    return bot.now_tehran().date()


def scan() -> int:
    df = bot.scan_nightly()
    df.to_csv(CAND, index=False, encoding="utf-8-sig")
    bot.log(f"nightly scan: {len(df)} symbols in the 0.25% band -> {CAND.name}")
    if len(df):
        bot.log(df.head(10).to_string(index=False))
    return 0


def live() -> int:
    """09:00 — full-market live scan (the new formula), then email the list."""
    live_df = bot.live_scan_full(workers=8, scope="locked", budget_sec=25)
    if live_df.empty:
        import pandas as _pd
        live_df = _pd.DataFrame(columns=["symbol", "ceiling", "live_bid", "ask",
                                         "dist_pct", "queue_qty", "queue_value"])
    live_df.to_csv(LIVE, index=False, encoding="utf-8-sig")
    body = [f"اسکن زندهٔ {bot.now_tehran().strftime('%H:%M:%S')} — فرمول فاصله تا سقف",
            "سقف = پایانی دیروز × (۱+نوسان) · قفل = بهترین خرید روی سقف و بدون فروشنده",
            f"تأییدشده: {len(live_df)}", ""]
    if len(live_df):
        for _, r in live_df.iterrows():
            body.append(f"  {r['symbol']}: صف {r['queue_value']/1e6:,.0f} م.ریال · "
                        f"قیمت {r['ceiling']:,.0f} · فاصله {r['dist_pct']}%")
        body += ["", "خرید در ۰۹:۰۱ روی سقف انجام می‌شود."]
    else:
        body.append("هیچ نمادی شرایط قفل + صف را ندارد؛ امروز خریدی انجام نمی‌شود.")
    text = "\n".join(body)
    print(text)
    send(f"[TseBot_Dist] اسکن زندهٔ {bot.now_tehran().strftime('%H:%M')}", text,
         key="dist-live", force=True)
    return 0 if len(live_df) else 2


def buy(dry: bool) -> int:
    """Buy the list produced by the 09:00 live scan (no fallback)."""
    import pandas as pd
    if not LIVE.exists():
        print("run live first"); return 1
    try:
        df = pd.read_csv(LIVE)
    except Exception:
        print("nothing confirmed"); return 1
    if df.empty:
        print("nothing confirmed"); return 1
    res = bot.buy(df, dry=dry)
    print(json.dumps(res, ensure_ascii=False, indent=1, default=str))
    return 0


def sell(dry: bool) -> int:
    print(json.dumps(bot.sell_loop(dry=dry), ensure_ascii=False, indent=1, default=str))
    return 0



def nightly_download() -> int:
    """Run the proven data refresh from the archived bot project."""
    import subprocess
    script = bot.LEGACY / "scripts" / "update_data.py"
    if not script.exists():
        bot.log(f"download script missing: {script}")
        return 1
    bot.log("nightly download started")
    proc = subprocess.run([sys.executable, "-u", str(script)], cwd=str(bot.LEGACY),
                          capture_output=True, text=True, timeout=3600)
    tail = ((proc.stdout or "") + (proc.stderr or ""))[-800:]
    bot.log(f"nightly download rc={proc.returncode} :: {tail[-300:]}")
    return proc.returncode


def nightly_scan_and_email() -> int:
    """Build tomorrow's band candidates and email them."""
    df = bot.scan_nightly()
    df.to_csv(CAND, index=False, encoding="utf-8-sig")
    bot.log(f"nightly scan: {len(df)} candidates")
    lines = [f"اسکن شبانهٔ {bot.now_tehran().strftime('%Y-%m-%d %H:%M')} — WarRobot",
             "باند فاصله تا سقف ۰٫۲۵٪ (قفل روی سقف)", ""]
    if len(df):
        for _, r in df.head(15).iterrows():
            lines.append(f"  {r['symbol']}: فاصله {r['dist_pct']}% · قیمت {r['ceiling']:,.0f}")
        lines += ["", f"مجموع: {len(df)} نماد", "خرید فردا ۰۸:۳۰:۰۱ روی سقف."]
    else:
        lines.append("هیچ نمادی در باند نبود؛ فردا خریدی انجام نمی‌شود.")
    text = "\n".join(lines)
    print(text)
    ok = send(f"[WarRobot] اسکن شبانه — {len(df)} کاندید", text, key="v3-nightly", force=True)
    bot.log(f"nightly email sent={ok}")
    return 0


def daemon() -> int:
    """Full schedule.

    08:45:01  enter the auction with the nightly band candidates (queue priority)
    09:00:02  cancel any order whose symbol is NOT locked on the live book
    09:01     top-up: retry the confirmed list every 10 s
    10:00     cancel everything still unfilled (frees the cash)
    """
    import pandas as pd
    b = bot.CONFIG["buy"]
    schedule = bot.CONFIG.get("schedule", {})
    done: set = set()
    bot.log("daemon started (auction-first)")
    while True:
        now = bot.now_tehran()
        hm, sec, day = now.strftime("%H:%M"), now.strftime("%S"), now.strftime("%Y-%m-%d")
        try:
            # 0) nightly pipeline: download at 20:30, scan + email at 21:20
            marker = bot.PROJ / "state" / "last_download.txt"
            already = marker.exists() and marker.read_text(encoding="utf-8").strip().startswith(day)
            if hm >= str(schedule.get("nightly_download", "20:30")) and (day, "dl") not in done and not already:
                done.add((day, "dl"))
                marker.write_text(f"{day}|running", encoding="utf-8")   # blocks a restart mid-download
                rc = nightly_download()
                marker.write_text(f"{day}|rc={rc}", encoding="utf-8")
                if rc != 0:
                    try:
                        send("[WarRobot] خطا در دانلود شبانه",
                             f"دانلود داده با کد {rc} تمام شد.\n"
                             f"زمان: {bot.now_tehran().strftime('%Y-%m-%d %H:%M')}\n"
                             "لطفاً لاگ update_data را بررسی کن.",
                             key="v3-dl-fail", force=True)
                    except Exception:
                        pass
            smarker = bot.PROJ / "state" / "last_scan.txt"
            scanned = smarker.exists() and smarker.read_text(encoding="utf-8").strip().startswith(day)
            if hm >= "21:15" and (day, "bfn") not in done:
                done.add((day, "bfn"))
                try:
                    import subprocess
                    pr = subprocess.run([sys.executable, "-u", str(bot.PROJ / "queue_scan.py")],
                                        cwd=str(bot.PROJ), capture_output=True, text=True, timeout=1800)
                    bot.log(f"queue nightly rc={pr.returncode} :: {((pr.stdout or '') + (pr.stderr or ''))[-160:]}")
                except Exception as exc:
                    bot.log(f"bfomul nightly error: {exc}")
            # the old band scan is retired: BFomul owns candidates.csv now
            if False and hm >= "21:20":
                pass

            # 2.9) 21:30 big/small ratio for tomorrow's candidates
            if hm >= "21:30" and (day, "bs") not in done:
                done.add((day, "bs"))
                try:
                    import subprocess
                    pr = subprocess.run([sys.executable, "-u", str(bot.PROJ / "big_small_nightly.py")],
                                        cwd=str(bot.PROJ), capture_output=True, text=True, timeout=1800)
                    bot.log(f"big_small rc={pr.returncode} :: {((pr.stdout or '') + (pr.stderr or ''))[-160:]}")
                    (bot.PROJ / "state" / "last_big_small.txt").write_text(day, encoding="utf-8")
                except Exception as exc:
                    bot.log(f"big_small error: {exc}")

            # 2.9) weekly per-symbol profiles (stage A + B), Sunday 20:10
            if now.weekday() == 6 and hm >= "20:10" and (day, "ab") not in done:
                done.add((day, "ab"))
                try:
                    import subprocess
                    pr = subprocess.run([sys.executable, "-u", str(bot.PROJ / "run_ab_all.py")],
                                        cwd=str(bot.PROJ), capture_output=True, text=True, timeout=3600)
                    bot.log(f"stage_ab rc={pr.returncode} :: {((pr.stdout or '') + (pr.stderr or ''))[-160:]}")
                except Exception as exc:
                    bot.log(f"stage_ab error: {exc}")

            # 2.94) quarterly BFomul rewrite: every 90 days, 20:00
            qmark = bot.PROJ / "state" / "last_bfomul_q.txt"
            qdue = True
            if qmark.exists():
                try:
                    from datetime import date as _d
                    last_q = _d.fromisoformat(qmark.read_text(encoding="utf-8").strip()[:10])
                    qdue = (now_tehran_date() - last_q).days >= 90
                except Exception:
                    qdue = True
            if qdue and hm >= "20:00" and (day, "bfq") not in done:
                done.add((day, "bfq"))
                try:
                    import subprocess
                    pr = subprocess.run([sys.executable, "-u", str(bot.PROJ / "bfomul_quarterly.py")],
                                        cwd=str(bot.PROJ), capture_output=True, text=True, timeout=3600)
                    bot.log(f"bfomul quarterly rc={pr.returncode} :: {((pr.stdout or '') + (pr.stderr or ''))[-200:]}")
                    qmark.write_text(bot.now_tehran().strftime("%Y-%m-%d"), encoding="utf-8")
                except Exception as exc:
                    bot.log(f"bfomul quarterly error: {exc}")

            # 2.91) nightly sell review + automatic exits (Playwright path)
            if hm >= "21:25" and (day, "sellrev") not in done:
                done.add((day, "sellrev"))
                try:
                    import subprocess as _s3
                    pr = _s3.run([sys.executable, "-u", str(bot.PROJ / "sell_monitor.py")],
                                 cwd=str(bot.PROJ), capture_output=True, text=True, timeout=1800)
                    bot.log(f"sell review rc={pr.returncode} :: "
                            f"{((pr.stdout or '') + (pr.stderr or ''))[-200:]}")
                except Exception as exc:
                    bot.log(f"sell review error: {str(exc)[:100]}")

            # 2.92) daily allocation advice (shadow, no trades)
            if hm >= "20:45" and (day, "adv") not in done:
                done.add((day, "adv"))
                try:
                    import subprocess
                    pr = subprocess.run([sys.executable, "-u", str(bot.PROJ / "alloc_advice.py")],
                                        cwd=str(bot.PROJ), capture_output=True, text=True, timeout=600)
                    bot.log(f"alloc advice rc={pr.returncode} :: {((pr.stdout or '') + (pr.stderr or ''))[-120:]}")
                except Exception as exc:
                    bot.log(f"alloc advice error: {exc}")

            # 2.93) weekly review of the CURRENT setup (Sunday 21:40)
            if now.weekday() == 6 and hm >= "21:40" and (day, "rev") not in done:
                done.add((day, "rev"))
                try:
                    import subprocess
                    pr = subprocess.run([sys.executable, "-u", str(bot.PROJ / "setup_report.py")],
                                        cwd=str(bot.PROJ), capture_output=True, text=True, timeout=1800)
                    bot.log(f"setup review rc={pr.returncode} :: {((pr.stdout or '') + (pr.stderr or ''))[-160:]}")
                except Exception as exc:
                    bot.log(f"setup review error: {exc}")

            # 2.95) weekly self-tuning: Sunday 21:40
            lmark = bot.PROJ / "state" / "last_learn.txt"
            due = True
            if lmark.exists():
                try:
                    from datetime import date as _date
                    last = _date.fromisoformat(lmark.read_text(encoding="utf-8").strip()[:10])
                    due = (now_tehran_date() - last).days >= 14
                except Exception:
                    due = True
            if False and due and now.weekday() == 6 and hm >= "21:40" and (day, "learn") not in done:
                done.add((day, "learn"))
                try:
                    import subprocess
                    pr = subprocess.run([sys.executable, "-u", str(bot.PROJ / "learn.py")],
                                        cwd=str(bot.PROJ), capture_output=True, text=True, timeout=900)
                    out = ((pr.stdout or "") + (pr.stderr or ""))[-400:]
                    bot.log(f"learn rc={pr.returncode} :: {out[-200:]}")
                    lmark.write_text(bot.now_tehran().strftime("%Y-%m-%d"), encoding="utf-8")
                    from tsebot.alerts import send as _send
                    _send("[WarRobot] بهینه‌سازی هفتگی وزن‌ها", out[:1500],
                          key="learn-weekly", force=True)
                except Exception as exc:
                    bot.log(f"learn error: {exc}")

            # 2.99) 22:45 firewall check of every scheduled job
            if hm >= "22:45" and (day, "wdog") not in done:
                done.add((day, "wdog"))
                try:
                    import subprocess
                    pr = subprocess.run([sys.executable, "-u", str(bot.PROJ / "watchdog.py")],
                                        cwd=str(bot.PROJ), capture_output=True, text=True, timeout=300)
                    bot.log(f"watchdog rc={pr.returncode} :: {((pr.stdout or '') + (pr.stderr or ''))[-200:]}")
                except Exception as exc:
                    bot.log(f"watchdog error: {exc}")

            # 3) 22:30 watchdog: the nightly download must have happened today
            if hm >= "22:30" and (day, "wd") not in done:
                done.add((day, "wd"))
                mk = bot.PROJ / "state" / "last_download.txt"
                ok = mk.exists() and mk.read_text(encoding="utf-8").strip().startswith(day)
                if not ok:
                    bot.log("WATCHDOG: nightly download missing today")
                    try:
                        send("[WarRobot] هشدار: دانلود شبانه انجام نشد",
                             f"ساعت {bot.now_tehran().strftime('%H:%M')} است و مارکر دانلود امروز "
                             f"({day}) وجود ندارد یا ناقص است.", key="v3-wd", force=True)
                    except Exception:
                        pass

            # 4) heartbeat every 30 minutes
            hb = (now.minute % 30 == 0 and now.second < 6)
            if hb and (day, f"hb{hm}") not in done:
                done.add((day, f"hb{hm}"))
                mk = bot.PROJ / "state" / "last_download.txt"
                dl = mk.read_text(encoding="utf-8").strip() if mk.exists() else "none"
                bot.log(f"heartbeat {hm} · download={dl} · candidates="
                         f"{len(pd.read_csv(CAND)) if CAND.exists() else 0}")

            # 0.8) 21:25 candidate tracker (14-day Excel follow-up)
            if hm >= "21:25" and (day, "trk") not in done:
                done.add((day, "trk"))
                try:
                    import subprocess
                    pr = subprocess.run([sys.executable, "-u", str(bot.PROJ / "tracker.py"), "--days", "2"],
                                        cwd=str(bot.PROJ), capture_output=True, text=True, timeout=600)
                    bot.log(f"tracker rc={pr.returncode} :: {((pr.stdout or '') + (pr.stderr or ''))[-200:]}")
                    (bot.PROJ / "state" / "last_tracker.txt").write_text(day, encoding="utf-8")
                except Exception as exc:
                    bot.log(f"tracker error: {exc}")

            # 0.7) 08:25 self-test of the whole order path (1 share, then cancel)
            if hm == "08:25" and (day, "st") not in done:
                done.add((day, "st"))
                bot.log("self-test 08:25 starting")
                try:
                    import subprocess
                    pr = subprocess.run([sys.executable, "-u", str(bot.PROJ / "selftest.py")],
                                        cwd=str(bot.PROJ), capture_output=True, text=True, timeout=240)
                    bot.log(f"self-test rc={pr.returncode} :: {((pr.stdout or '') + (pr.stderr or ''))[-300:]}")
                    (bot.PROJ / "state" / "last_selftest.txt").write_text(day, encoding="utf-8")
                except Exception as exc:
                    bot.log(f"self-test error: {exc}")

            # 0.5) session watchdog during the auction: email if Chrome leaves the
            #      broker page between 08:25 and 09:10
            if "08:25" <= hm <= "09:10":
                if (day, f"ss{hm}{sec[:1]}") not in done:
                    done.add((day, f"ss{hm}{sec[:1]}"))
                    st = bot.session_state()
                    if st != "ok":
                        bot.log(f"SESSION {st} during auction — alert email")
                        try:
                            send(f"[WarRobot] قطع نشست کارگزاری در حراج ({st})",
                                 "ساعت " + hm + ":" + sec + " · وضعیت: " + st + "\n"
                                 "صفحه کارگزاری از دسترس خارج شده. لطفاً سریع وارد شو:\n"
                                 "https://online.meibours.ir/Home/Default2",
                                 key="v3-session", force=True)
                        except Exception:
                            pass

            # 0.9) order-book sampler: one row per candidate per minute in session
            if "08:30" <= hm <= "12:25" and (day, f"bk{hm}{sec[:1]}") not in done:
                done.add((day, f"bk{hm}{sec[:1]}"))
                try:
                    import subprocess
                    subprocess.run([sys.executable, "-u", str(bot.PROJ / "book_recorder.py")],
                                   cwd=str(bot.PROJ), capture_output=True, text=True, timeout=120)
                except Exception as exc:
                    bot.log(f"book error: {exc}")

            if (day, f"diag{hm}") not in done:
                done.add((day, f"diag{hm}"))
                bot.log(f"diag hm={hm} sec={sec} bids={len(done)}")

            # 0.95) pre-flight at 08:40: session, bot, order capability
            if "08:40" <= hm < "08:45" and (day, "pre") not in done:
                done.add((day, "pre"))
                try:
                    import subprocess
                    pr = subprocess.run([sys.executable, "-u", str(bot.PROJ / "preflight.py")],
                                        cwd=str(bot.PROJ), capture_output=True, text=True, timeout=900)
                    txt = ((pr.stdout or "") + (pr.stderr or "")).strip()
                    bot.log(f"preflight rc={pr.returncode} :: {txt[-180:]}")
                    if '"ready": false' in txt or pr.returncode != 0:
                        try:
                            from tsebot.alerts import send as _send
                            _send("[WarRobot] آماده‌سازی ۰۸:۴۰ ناموفق",
                                  "بررسی پیش از سفارش‌گذاری مشکل داشت:\n" + txt[-500:],
                                  key="preflight", force=True)
                        except Exception:
                            pass
                except Exception as exc:
                    bot.log(f"preflight error: {exc}")

            # 1) single-shot unattended ordering at exactly 08:45:01
            # the whole 08:45 minute is accepted (the loop may tick slower than 1s)
            if hm == b.get("preopen_attempt", "08:45") and (day, "pwbuy") not in done:
                done.add((day, "pwbuy"))
                try:
                    import subprocess as _sp
                    pr = _sp.run([sys.executable, "-u", str(bot.PROJ / "order_candidates.py")],
                                 cwd=str(bot.PROJ), capture_output=True, text=True, timeout=1800)
                    bot.log(f"unattended order rc={pr.returncode} :: "
                            f"{((pr.stdout or '') + (pr.stderr or ''))[-200:]}")
                except Exception as exc:
                    bot.log(f"unattended order error: {str(exc)[:100]}")

            # 1b) auction entry from 08:30:01, retried every N seconds until 09:00
            start_hm = b["preopen_attempt"]
            until_hm = b.get("preopen_until", "09:00")
            retry = int(b.get("preopen_retry_sec", 15))
            bucket = (now.minute * 60 + now.second) // max(retry, 1)
            if start_hm <= hm < until_hm and (now.minute * 60 + now.second) % retry < 5:
                key = (day, f"auc{bucket}")   # one attempt per retry bucket (no double orders)
                if key not in done:
                    done.add(key)
                    bot.log(f"auction entry {hm}:{sec} (nightly band list)")
                    if CAND.exists():
                        df = pd.read_csv(CAND).head(int(bot.CONFIG["universe"]["max_picks_per_day"]))
                        if len(df):
                            print(json.dumps(bot.buy(df, dry=False), ensure_ascii=False,
                                             indent=1, default=str))

            # 2) 09:00:02 cancel orders whose stock is not locked
            guard = str(b.get("cancel_if_not_locked_at", "09:00:02"))
            g_hm, _, g_sec = guard.partition(":")
            if hm == g_hm and sec >= (g_sec or "02") and (day, "protect") not in done:
                done.add((day, "protect"))
                live = bot.live_scan_full(workers=8, scope="locked", budget_sec=20)
                locked = set(live.symbol) if len(live) else set()
                led = bot.ledger()
                held = {p.symbol for p in led.open_positions()}
                stale = [s for s in held if s not in locked]
                if stale:
                    bot.cancel_open(stale)

            # 3) 09:01-10:00 top-up retries from the live list
            if b["window_start"] <= hm <= b["window_end"] and (day, f"t{hm}") not in done:
                done.add((day, f"t{hm}"))
                try:
                    ldf = pd.read_csv(LIVE) if LIVE.exists() else None
                    if ldf is not None and not ldf.empty:
                        bot.buy(ldf, dry=False)
                except Exception:
                    pass

            # 3.9) 10:00 rotate: cancel the unfilled orders and order the next candidates
            if hm >= b.get("cancel_unfilled_at", "10:00") and (day, "rebal") not in done:
                done.add((day, "rebal"))
                try:
                    import subprocess as _s2
                    pr = _s2.run([sys.executable, "-u", str(bot.PROJ / "rebalance.py")],
                                 cwd=str(bot.PROJ), capture_output=True, text=True, timeout=1800)
                    bot.log(f"10:00 rotate rc={pr.returncode} :: "
                            f"{((pr.stdout or '') + (pr.stderr or ''))[-200:]}")
                except Exception as exc:
                    bot.log(f"10:00 rotate error: {str(exc)[:100]}")

            # 4) 10:00 cancel everything unfilled
            if False and hm >= b.get("cancel_unfilled_at", "10:00") and (day, "cancel10") not in done:
                done.add((day, "cancel10"))
                try:
                    import subprocess
                    pr = subprocess.run([sys.executable, "-u", str(bot.PROJ / "rotate_unfilled.py")],
                                        cwd=str(bot.PROJ), capture_output=True, text=True, timeout=1200)
                    bot.log(f"10:00 cancel+rotate rc={pr.returncode} :: "
                            f"{((pr.stdout or '') + (pr.stderr or ''))[-200:]}")
                except Exception as exc:
                    bot.log(f"10:00 cancel+rotate error: {exc}")

            # daily sell loop
            if (day, f"s{hm}") not in done:
                done.add((day, f"s{hm}"))
                try:
                    bot.sell_loop(dry=False)
                except Exception as exc:
                    bot.log(f"sell loop error: {exc}")
        except Exception as exc:
            bot.log(f"loop error: {exc}")
        time.sleep(5)


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("scan", "live", "daemon"):
        sub.add_parser(name)
    for name in ("buy", "sell"):
        p = sub.add_parser(name)
        p.add_argument("--dry", action="store_true")
    a = ap.parse_args()
    if a.cmd == "scan":
        return scan()
    if a.cmd == "live":
        return live()
    if a.cmd == "buy":
        return buy(a.dry)
    if a.cmd == "sell":
        return sell(a.dry)
    return daemon()


if __name__ == "__main__":
    raise SystemExit(main())
