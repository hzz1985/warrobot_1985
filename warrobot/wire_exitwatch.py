"""Add repeating intraday jobs to the scheduler (exit watch every 3 minutes)."""
from pathlib import Path

p = Path(r"C:\Users\Administrator\Desktop\DeepSeek DeskTop\WarRobot\scheduler.py")
s = p.read_text(encoding="utf-8")

if "REPEAT" not in s:
    # 1) the repeating job table
    s = s.replace('''def _load() -> dict:''',
'''# jobs that run over and over inside a time window: (from, to, every_min, name, script)
REPEAT = [
    ("09:00", "12:30", 3, "exitwatch", "intraday_exit.py"),
]


def _load() -> dict:''', 1)

    # 2) fire the repeating jobs each tick
    s = s.replace('''        # prune old days''',
'''        # repeating jobs (e.g. the take-profit / stop-loss watch)
        for at_from, at_to, every, job, script in REPEAT:
            if not (at_from <= hm <= at_to):
                continue
            bucket = _minutes(hm) // max(1, every)
            key = f"{job}-{bucket}"
            if key in today:
                continue
            today[key] = hm
            _save(data)
            _run(job, script)

        # prune old days''', 1)

    p.write_text(s, encoding="utf-8")
    print("repeating exit-watch job added (09:00-12:30 every 3 min)")
else:
    print("already present")
