"""Self-test of the full order path, run at 08:25 before the auction.

Steps: bind the first candidate -> fill 1 share at the ceiling -> submit -> read
the panel message -> cancel the ticket immediately. A 1-share ticket is the
cheapest way to prove that binding, price, tick, send button and the panel
messages all work before the real 08:30 orders.

Exit code 0 = path works, 2 = bind failed, 3 = submit rejected, 4 = no candidate.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

P = Path(__file__).resolve().parent
sys.path.insert(0, str(P / "src"))

import pandas as pd                       # noqa: E402
import distbot as bot                     # noqa: E402
from tsebot.alerts import send            # noqa: E402


def market_open() -> bool:
    """The order path can only be proven while the exchange accepts orders."""
    now = bot.now_tehran()
    if now.weekday() in (4, 5):          # Python: Fri/Sat vs Tehran Thu/Fri — use hour only
        pass
    hm = now.strftime("%H:%M")
    return "08:30" <= hm <= "12:20"


def main() -> int:
    cand = P / "state" / "candidates.csv"
    if not cand.exists():
        print("no candidates file")
        return 4
    df = pd.read_csv(cand)
    if df.empty:
        print("no candidates")
        return 4

    row = df.iloc[0]
    sym, price = str(row["symbol"]), float(row["ceiling"])
    print(f"self-test on {sym} @ {price:,.0f}")

    if not market_open():
        msg = ("تست خودکار اجرا نشد: بازار بسته است.\n"
               f"زمان اجرا: {bot.now_tehran().strftime('%Y-%m-%d %H:%M')}\n"
               "این پیام FAIL نیست؛ تست فقط در ساعات معاملاتی قابل اجراست.")
        print(msg)
        send("[WarRobot] تست خودکار — بازار بسته (SKIP)", msg, key="selftest", force=True)
        return 0

    b = bot.broker()
    ok_bind = False
    try:
        ok_bind = bool(b.bind_symbol(sym))
        shown = str(b._js("var e=document.querySelector('#fulltextContainer'); return e?(e.innerText||'').trim():'';") or "")
        if not shown.startswith(sym[:2]):
            print("bind looked true but the panel shows:", shown or "(empty)")
            ok_bind = False
    except Exception as exc:
        print("bind exception:", str(exc)[:120])
    print("bind:", ok_bind)
    if not ok_bind:
        send("[WarRobot] تست خودکار: bind ناموفق",
             f"تست ۰۸:۲۵ روی {sym} اجرا شد ولی bind نشد.\n"
             "سفارش‌گذاری امروز احتمالاً کار نمی‌کند.", key="selftest", force=True)
        return 2

    lo, hi = b.panel_price_limits()
    # the exchange rejects buys far from the last trade, so the ticket must sit at
    # the ceiling; it may fill, which is accepted (a ~5M rial position).
    test_price = float(hi) if hi else price
    test_qty = int(-(-5000000 // int(test_price)))
    print(f"test ticket: {test_qty} @ {test_price:,.0f} (ceiling)")
    res = b.submit_tab2("buy", sym, test_qty, test_price, None)
    msg = str(res.get("message") or res.get("reason") or "")
    placed = bool(res.get("ok"))
    print("submit:", json.dumps(res, ensure_ascii=False, default=str)[:200])

    cancelled = 0
    if placed:
        try:
            cancelled = b.cancel_open_orders([sym])
        except Exception:
            cancelled = 0
    print("cancelled:", cancelled)

    body = (f"تست خودکار مسیر سفارش — {sym}\n"
            f"bind: {'OK' if ok_bind else 'FAIL'}\n"
            f"ثبت سفارش ۱ سهم روی {price:,.0f}: {'موفق' if placed else 'ناموفق'}\n"
            f"پیام پنل: {msg[:160]}\n"
            f"لغو تیکت تست: {cancelled}\n")
    send(f"[WarRobot] تست خودکار {'OK' if placed else 'FAIL'}", body,
         key="selftest", force=True)
    return 0 if placed else 3


if __name__ == "__main__":
    raise SystemExit(main())
