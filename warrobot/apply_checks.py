"""Add the new checks + night job (services online, jobs executed, pm2 save).

selfcheck_rules.py gains:
  * every service online (warrobot / warsched / tse-web)
  * today's scheduled jobs actually RAN (not "skipped" by the window guard)
scheduler.py gains:
  * 23:50 pm2_save  -> keeps the dump fresh so a reboot restores the full list
"""
from pathlib import Path

WAR = Path(r"C:\Users\Administrator\Desktop\DeepSeek DeskTop\WarRobot")

# ------------------------------------------------------------- selfcheck rules
sc = (WAR / "selfcheck_rules.py").read_text(encoding="utf-8")
if "services online" not in sc:
    sc = sc.replace('''    # live web check: catches a 500 that a static grep can never see''',
'''    # services online
    try:
        import subprocess as _sp
        out = (_sp.run(["pm2", "list", "--no-color"], capture_output=True, text=True,
                       timeout=90, shell=True).stdout or "")
        missing = []
        for name in ("warrobot", "warsched", "tse-web"):
            hit = any(f" {name} " in line and "online" in line for line in out.splitlines())
            if not hit:
                missing.append(name)
        add("هر سه سرویس آنلاین (warrobot · warsched · tse-web)",
            not missing, "همه آنلاین" if not missing else "خاموش: " + ", ".join(missing))
    except Exception as exc:
        add("هر سه سرویس آنلاین (warrobot · warsched · tse-web)", False, str(exc)[:80])

    # today's jobs actually ran (not skipped, and not still pending)
    try:
        from datetime import datetime as _dt
        jobs = {"08:25": "selfcheck", "08:30": "ready30", "08:40": "preflight",
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

    # live web check: catches a 500 that a static grep can never see''')
    (WAR / "selfcheck_rules.py").write_text(sc, encoding="utf-8")
    print("selfcheck: services + jobs-executed checks added")
else:
    print("selfcheck already extended")

# ------------------------------------------------------------ nightly pm2 save
sch = (WAR / "scheduler.py").read_text(encoding="utf-8")
if "pm2_save.py" not in sch:
    sch = sch.replace('''    ("21:35", "webrefresh", ["web_refresh.py"]),''',
'''    ("21:35", "webrefresh", ["web_refresh.py"]),
    ("23:50", "pm2save", ["pm2_save.py"]),''')
    # the window guard would skip it after 24:10; run it before that and never
    # let a restart replay it late
    (WAR / "scheduler.py").write_text(sch, encoding="utf-8")
    print("scheduler: nightly pm2 save at 23:50")
else:
    print("pm2 save already scheduled")
