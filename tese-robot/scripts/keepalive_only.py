"""Keep-alive only: no trading, no quoting, no network.

Attaches to the already-open Chrome, synthesises a mouse move every 30 seconds
and records whether the tab is still on an authenticated page.
"""
import sys
import time
from datetime import datetime

sys.path.insert(0, r"C:\Users\Administrator\Desktop\DeepSeek DeskTop\TeseRobot\src")

from tsebot.broker_selenium import SeleniumBroker  # noqa: E402

LOG = r"C:\Users\Administrator\Desktop\DeepSeek DeskTop\TeseRobot\logs\keepalive_only.log"


def w(msg: str) -> None:
    line = f"{datetime.now().strftime('%H:%M:%S')}  {msg}"
    with open(LOG, "a", encoding="utf-8") as fh:
        fh.write(line + "\n")


b = SeleniumBroker()
b.connect()
w("attached")
for i in range(24):  # 12 minutes
    try:
        status = b.keepalive()
    except Exception as exc:
        status = f"EXC {str(exc)[:60]}"
    w(f"nudge {i + 1}: {status}")
    time.sleep(30)
w("done")
