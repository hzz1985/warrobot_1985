"""14-day candidate tracker in Excel.

Every session the band-0.25% candidates are appended to state/candidates_tracker.xlsx
and then tracked forward: each row records the real OHLC of the following sessions,
the return from the entry (next open) and the best/worst excursion. Rows stay for
14 sessions and are refreshed daily.

    python tracker.py --days 3        # backfill the last 3 sessions
    python tracker.py                 # today only
"""
from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path

import pandas as pd
from openpyxl import Workbook, load_workbook

P_ = Path(__file__).resolve().parent
BASE = Path(r"C:\Users\Administrator\Desktop\DeepSeek DeskTop")
DAILY = BASE / "TeseRobot" / "data" / "daily"
XLSX = BASE / "WarRobot" / "state" / "candidates_tracker.xlsx"
BAND = 0.25
LIMIT = 3.0
TRACK_DAYS = 14

PROFILE_COLS = ["close_pos_median", "lock_freq_pct", "p_lock_rsi60plus",
                "p_lock_macd_pos", "p_lock_bb_top"]
COLS = ["session", "symbol"] + PROFILE_COLS + ["bought_today", "buy_qty", "big_to_small", "queue_value", "close", "ceiling_yday", "dist_pct", "volume", "turnover",
        "next_ceiling", "entry_open", "t1_high", "t1_low", "t1_close",
        "ret_t1_pct", "best_pct", "worst_pct", "sessions_tracked",
        "tp_hit", "sl_hit", "days_to_exit", "ret_final_pct", "exit_reason", "status", "updated"]
DAILY_COLS = ["session", "symbol", "day_index", "date", "close", "ret_from_entry_pct"]


def load_daily() -> dict:
    out = {}
    for f in DAILY.glob("*_daily.csv"):
        sym = f.name[:-len("_daily.csv")]
        try:
            d = pd.read_csv(f, dtype={"date": str})
        except Exception:
            continue
        if len(d) < 5:
            continue
        out[sym] = d.sort_values("date").reset_index(drop=True)
    return out


def candidates_for(d: pd.DataFrame, session: str) -> list:
    """Band candidates that were produced by the close BEFORE `session`."""
    rows = []
    if session not in set(d["date"]):
        return rows
    i = int(d.index[d["date"] == session][0])
    if i == 0:
        return rows
    prev = float(d["Close"].iloc[i - 1])
    close = float(d["Close"].iloc[i])
    vol = float(d["Volume"].iloc[i])
    ceil_y = float(round(prev * (1 + LIMIT / 100)))
    dist = (ceil_y - close) / close * 100
    if dist <= BAND:
        rows.append({"session": session, "close": close, "ceiling_yday": ceil_y,
                     "dist_pct": round(dist, 3), "volume": vol, "turnover": vol * close,
                     "next_ceiling": float(round(close * (1 + LIMIT / 100)))})
    return rows


def track_full(d: pd.DataFrame, session: str) -> list:
    """Daily rows for the 14 sessions after `session` (close vs entry)."""
    dates = list(d["date"])
    if session not in dates:
        return []
    i = dates.index(session)
    fwd = d.iloc[i + 1: i + 1 + TRACK_DAYS]
    if fwd.empty:
        return []
    entry = float(fwd["Open"].iloc[0])
    if entry <= 0:
        return []
    return [{"day_index": k + 1, "date": str(r["date"]),
             "close": round(float(r["Close"]), 2),
             "ret_from_entry_pct": round((float(r["Close"]) - entry) / entry * 100, 2)}
            for k, (_, r) in enumerate(fwd.iterrows())]


def tp_sl_path(d: pd.DataFrame, session: str, tp: float = 15.0, sl: float = 4.0) -> dict:
    dates = list(d["date"])
    if session not in dates:
        return {}
    i = dates.index(session)
    fwd = d.iloc[i + 1: i + 1 + TRACK_DAYS]
    if fwd.empty:
        return {}
    entry = float(fwd["Open"].iloc[0])
    if entry <= 0:
        return {}
    for k, (_, r) in enumerate(fwd.iterrows()):
        if float(r["High"]) >= entry * (1 + tp / 100):
            return {"tp_hit": 1, "sl_hit": 0, "days_to_exit": k + 1,
                    "ret_final_pct": round(tp - 2.0, 2), "exit_reason": "حد سود"}
        if float(r["Low"]) <= entry * (1 - sl / 100):
            return {"tp_hit": 0, "sl_hit": 1, "days_to_exit": k + 1,
                    "ret_final_pct": round(-sl - 2.0, 2), "exit_reason": "حد ضرر"}
    last = float(fwd["Close"].iloc[-1])
    return {"tp_hit": 0, "sl_hit": 0, "days_to_exit": len(fwd),
            "ret_final_pct": round((last - entry) / entry * 100 - 2.0, 2),
            "exit_reason": "پایان دوره" if len(fwd) < TRACK_DAYS else "زمان"}


def track(d: pd.DataFrame, session: str, row: dict) -> dict:
    """Fill the forward columns from the sessions after `session`."""
    dates = list(d["date"])
    if session not in dates:
        return row
    i = dates.index(session)
    fwd = d.iloc[i + 1: i + 1 + TRACK_DAYS]
    if fwd.empty:
        row["status"] = "منتظر"
        return row
    entry = float(fwd["Open"].iloc[0])
    if entry <= 0:
        return row
    row.update({
        "entry_open": round(entry, 2),
        "t1_high": round(float(fwd["High"].iloc[0]), 2),
        "t1_low": round(float(fwd["Low"].iloc[0]), 2),
        "t1_close": round(float(fwd["Close"].iloc[0]), 2),
        "ret_t1_pct": round((float(fwd["Close"].iloc[0]) - entry) / entry * 100, 2),
        "best_pct": round((float(fwd["High"].max()) - entry) / entry * 100, 2),
        "worst_pct": round((float(fwd["Low"].min()) - entry) / entry * 100, 2),
        "sessions_tracked": len(fwd),
        "status": "در حال ردیابی" if len(fwd) < TRACK_DAYS else "کامل",
    })
    return row




def broker_buys() -> dict:
    """{symbol: qty bought today} from the broker portfolio, or {} if no session."""
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
    try:
        import distbot as bot
        if bot.session_state() != "ok":
            return {}
        b = bot.broker()
        raw = b._js("""
        var out={};
        document.querySelectorAll('table tr').forEach(function(tr){
          var tds=tr.querySelectorAll('td');
          if(tds.length<5) return;
          var sym=(tds[0].innerText||'').trim();
          if(!sym || sym.length>12) return;
          var t=(tr.innerText||'').replace(/\\s+/g,' ');
          var m=t.match(/خرید امروز\\s*([\\d,]+)/);
          if(m) out[sym]=parseInt(m[1].replace(/,/g,''),10);
        });
        return JSON.stringify(out);
        """)
        import json
        return {k: int(v) for k, v in json.loads(raw or "{}").items() if v}
    except Exception:
        return {}


def remind_login() -> None:
    """Email a reminder when the broker session is not active at nightly time."""
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
    try:
        import distbot as bot
        from tsebot.alerts import send
        st = bot.session_state()
        if st == "ok":
            print("session ok — no reminder needed")
            return
        # try the automatic login first; only nag if it really fails
        if st == "logged_out":
            fixed = bot.ensure_session()
            if fixed == "ok":
                print("auto-login fixed the session — no reminder")
                return
        st2 = bot.session_state()
        if st2 == "ok":
            print("session recovered — no reminder")
            return
        send("[WarRobot] یادآوری: ورود به پنل کارگزاری",
             "ورود خودکار نشد (" + st2 + ").\n"
             "لطفاً یک بار وارد شو: https://online.meibours.ir/Home/Default2",
             key="nightly-login", force=True)
        print("login reminder sent:", st2)
    except Exception as exc:
        print("reminder error:", str(exc)[:80])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=2)
    a = ap.parse_args()

    daily = load_daily()
    sessions = sorted({str(x) for d in daily.values() for x in d["date"].iloc[-3:].tolist()})[-a.days:]
    print("sessions:", sessions)

    rows = []
    for session in sessions:
        for sym, d in daily.items():
            for r in candidates_for(d, session):
                r["symbol"] = sym
                r = track(d, session, r)
                rows.append(r)
    if not rows:
        print("nothing to add")
        return 1
    remind_login()
    buys = broker_buys()
    try:
        import json as _json
        bs = _json.loads((P_ / "state" / "big_small.json").read_text(encoding="utf-8"))
    except Exception:
        bs = {}
    live = {}
    lc = P_ / "state" / "live_candidates.csv"
    if lc.exists():
        try:
            ld = pd.read_csv(lc)
            live = {str(r["symbol"]): float(r.get("queue_value") or 0) for _, r in ld.iterrows()}
        except Exception:
            live = {}
    for r in rows:
        sym = r.get("symbol")
        r["bought_today"] = 1 if buys.get(sym) else 0
        r["buy_qty"] = buys.get(sym, 0)
        r["big_to_small"] = (bs.get(sym) or {}).get("ratio")
        r["queue_value"] = live.get(sym)
        info = tp_sl_path(daily.get(sym), r["session"])
        r.update(info)
    df = pd.DataFrame(rows)

    if XLSX.exists():
        old = pd.read_excel(XLSX, sheet_name="candidates")
    else:
        old = pd.DataFrame(columns=COLS)
    merged = pd.concat([old, df], ignore_index=True)
    merged["updated"] = datetime.now().strftime("%Y-%m-%d %H:%M")
    merged = merged.drop_duplicates(subset=["session", "symbol"], keep="last")
    merged = merged.sort_values(["session", "turnover"], ascending=[False, False])
    merged = merged.reindex(columns=COLS)

    daily_rows = []
    for sym, d in daily.items():
        for sess in sessions:
            for rec in track_full(d, sess):
                rec["session"] = sess
                rec["symbol"] = sym
                daily_rows.append(rec)
    dd = pd.DataFrame(daily_rows)

    with pd.ExcelWriter(XLSX, engine="openpyxl") as xw:
        merged.to_excel(xw, sheet_name="candidates", index=False)
        per = (merged.groupby("session")
               .agg(تعداد=("symbol", "count"),
                    میانگین_فاصله=("dist_pct", "mean"),
                    میانگین_بازده_T1=("ret_t1_pct", "mean"))
               .reset_index())
        per.to_excel(xw, sheet_name="summary", index=False)
        if len(dd):
            dd.reindex(columns=DAILY_COLS + ["symbol"]).to_excel(
                xw, sheet_name="daily_track", index=False)

    print(f"rows: {len(merged)} (added {len(df)}) -> {XLSX}")
    print(merged.groupby("session").size().to_string())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
