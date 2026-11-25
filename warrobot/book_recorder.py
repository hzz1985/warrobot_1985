"""Record the live order book for every candidate — the missing dataset.

The deep question ("how much supply sits at the ceiling, and does the queue hold?")
cannot be answered from trades alone: we need the BOOK over time. This recorder
samples BestLimits for all candidates every minute during the session and appends
one row per symbol per sample to state/book.db.

After a few weeks this yields, per stock:
    * supply at the ceiling (ask volume at the ceiling price)
    * queue depth at the ceiling (bid volume)
    * how the queue evolves minute by minute (durability signal)
    * the moment the lock breaks
"""
from __future__ import annotations

import sqlite3
import sys
from datetime import datetime
from pathlib import Path

P = Path(__file__).resolve().parent
sys.path.insert(0, str(P / "src"))
sys.path.insert(0, str(P.parent / "Tse2" / "orderflow"))

import pandas as pd                 # noqa: E402
import tsetmc as api                # noqa: E402
import distbot as bot               # noqa: E402

DB = P / "state" / "book.db"


def ensure() -> sqlite3.Connection:
    con = sqlite3.connect(DB, timeout=30)
    con.execute("""CREATE TABLE IF NOT EXISTS book(
        ts TEXT, session TEXT, symbol TEXT, ins_code TEXT,
        best_bid REAL, best_ask REAL, bid_vol REAL, ask_vol REAL,
        bid_val REAL, ask_val REAL, levels TEXT,
        PRIMARY KEY (ts, symbol))""")
    con.execute("CREATE INDEX IF NOT EXISTS ix_book_sym ON book(symbol, ts)")
    con.commit()
    return con


def main() -> int:
    hm = bot.now_tehran().strftime("%H:%M")
    if not ("08:30" <= hm <= "12:25"):
        return 0
    cand = P / "state" / "candidates.csv"
    if not cand.exists():
        return 1
    df = pd.read_csv(cand).head(60)          # the ones that can actually be bought
    con = ensure()
    ts = bot.now_tehran().strftime("%Y-%m-%d %H:%M:%S")
    session = str(df["session"].iloc[0]) if len(df) else ""
    n = 0
    for _, r in df.iterrows():
        sym = str(r["symbol"])
        try:
            inst = api.resolve(sym)
            if not inst:
                continue
            payload = api.best_limits(inst["ins_code"])
            b = api.parse_book(payload)
            if not b["best_bid"] and not b["best_ask"]:
                continue                      # no book yet (halted / not trading)
            con.execute("""INSERT OR REPLACE INTO book
                (ts, session, symbol, ins_code, best_bid, best_ask, bid_vol, ask_vol,
                 bid_val, ask_val, levels) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                        (ts, session, sym, inst["ins_code"], b["best_bid"], b["best_ask"],
                         b["bid_volume_5"], b["ask_volume_5"],
                         b["best_bid"] * b["bid_volume_5"], b["best_ask"] * b["ask_volume_5"],
                         str(payload)[:2000]))
            n += 1
        except Exception:
            continue
    con.commit()
    con.close()
    bot.log(f"book sample {ts[-8:]} · {n} symbols")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
