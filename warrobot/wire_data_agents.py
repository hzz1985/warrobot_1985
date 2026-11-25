from pathlib import Path

SC = Path(r"C:\Users\Administrator\Desktop\DeepSeek DeskTop\WarRobot\scheduler.py")
s = SC.read_text(encoding="utf-8")

# 1) run repeat jobs through a generic runner so they can carry arguments
if "parts = script.split()" not in s:
    s = s.replace("""        for at_from, at_to, every, job, script in REPEAT:""",
"""        for at_from, at_to, every, job, script in REPEAT:""")
    s = s.replace("""            _run(job, script, [])""",
"""            _parts = str(script).split()
            _run(job, _parts[0], _parts[1:])""")

# 2) data agents keep refreshing outside market hours too
if "d_balance" not in s:
    s = s.replace("""            if not trading and job not in ("guard",):
                break""",
"""            if not trading and job not in ("guard", "d_balance",
                                           "d_portfolio", "d_pnl"):
                break""")
    anchor = 'REPEAT = ['
    s = s.replace(anchor, anchor + """
    # per-card data agents (their own timers)
    ("09:00", "12:30", 5, "d_balance", "agents/data_agents.py --job balance"),
    ("12:35", "23:59", 30, "d_balance_off", "agents/data_agents.py --job balance"),
    ("00:00", "08:59", 30, "d_balance_night", "agents/data_agents.py --job balance"),
    ("09:00", "12:30", 10, "d_portfolio", "agents/data_agents.py --job portfolio"),
    ("12:35", "23:59", 60, "d_portfolio_off", "agents/data_agents.py --job portfolio"),
    ("09:00", "12:30", 15, "d_pnl", "agents/data_agents.py --job pnl"),
    ("12:35", "23:59", 60, "d_pnl_off", "agents/data_agents.py --job pnl"),""", 1)
    SC.write_text(s, encoding="utf-8")
    print("scheduler: per-card data agent timers wired")
else:
    print("already wired")

# 3) selfcheck rule
R = Path(r"C:\Users\Administrator\Desktop\DeepSeek DeskTop\WarRobot\selfcheck_rules.py")
q = R.read_text(encoding="utf-8")
if "ایجنت دادهٔ هر کارت" not in q:
    q = q.replace('''    add("تقویم رسمی (TSETMC) اولویت دارد",''',
'''    add("ایجنت دادهٔ هر کارت (موجودی/پرتفوی/P&L) زمان‌بند دارد",
        (BASE / "agents" / "data_agents.py").exists()
        and "data_agents.py --job balance" in (BASE / "scheduler.py").read_text(encoding="utf-8"),
        "agents/data_agents.py")
    add("تقویم رسمی (TSETMC) اولویت دارد",''')
    R.write_text(q, encoding="utf-8")
    print("rule added")
