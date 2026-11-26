"""Nightly queue scan — the setup that survived the 4-year walk-forward.

Rule
  candidate : the session closed LOCKED on its ceiling
              (High == Close and High >= previous close * 1.029)
  entry     : next session, at the ceiling, only if the price trades there
  exit      : +25% / -3% / 15 sessions
  sizing    : 1/3 of the available cash per position (max 3 positions)
  ranking   : NONE — the backtest opened positions in data-file order, so the
              live scan takes the first symbols in the same order

Writes state/candidates.csv (what the bot buys) and appends every candidate to
Desktop/BFormulExcel.xlsx for the 14-day follow-up.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

BASE = Path(r"C:\Users\Administrator\Desktop\DeepSeek DeskTop")
PROJ = BASE / "WarRobot"
DAILY = BASE / "TeseRobot" / "data" / "daily"
D3Y = BASE / "Tse2" / "orderflow" / "data" / "daily3y"
XLSX = Path(r"C:\Users\Administrator\Desktop\DeepSeek DeskTop\WarRobot\data\BFormulExcel.xlsx")
CAND = PROJ / "state" / "candidates.csv"
sys.path.insert(0, str(BASE / "TeseRobot" / "src"))

TP, SL, HOLD, MAX_PICK = 25.0, 3.0, 15, 6


def load(sym: str):
    for p in (DAILY / f"{sym}_daily.csv", D3Y / f"{sym}.csv"):
        if p.exists():
            try:
                return pd.read_csv(p, dtype={"date": str}).sort_values("date").reset_index(drop=True)
            except Exception:
                return None
    return None


def main() -> int:
    rows = []
    # EXACT backtest order: iterate the 3-year data folder as the OS returns it,
    # then append any symbol that only exists in the daily folder.
    syms, seen = [], set()
    for f in D3Y.glob("*.csv"):
        if f.stem not in seen:
            seen.add(f.stem)
            syms.append(f.stem)
    for f in DAILY.glob("*_daily.csv"):
        name = f.name[:-len("_daily.csv")]
        if name not in seen:
            seen.add(name)
            syms.append(name)
    for sym in syms:
        d = load(sym)
        if d is None or len(d) < 6:
            continue
        i = len(d) - 1
        last, prev = d.iloc[i], d.iloc[i - 1]
        try:
            prev_close = float(prev["Close"])
            high = float(last["High"])
            close = float(last["Close"])
            vol = float(last["Volume"])
        except Exception:
            continue
        if prev_close <= 0 or close <= 0:
            continue
        locked = (high == close) and (high >= prev_close * 1.029)
        if not locked:
            continue
        ceiling_today = round(prev_close * 1.03)
        rows.append({"symbol": sym, "close": close, "ceiling": float(ceiling_today),
                     "next_ceiling": float(round(close * 1.03)), "dist_pct": 0.0,
                     "volume": vol, "turnover": vol * close, "session": str(last["date"]),
                     "tp_pct": TP, "sl_pct": SL, "hold_days": HOLD,
                     "reason": f"قفل روی سقف {ceiling_today:,.0f} · حجم {vol/1e6:.1f}M"})
    all_locked = pd.DataFrame(rows)
    # Hark V3 (2026-11-26): strategy switch. config.json strategy.version v2/v3 filters and ranks
    # the locked list (v3 = v2 filters + skip known loss-makers, rank by individual buyer power,
    # see strategy_v3.py). v1, or any error here, keeps the old data-file order.
    try:
        import json as _jv
        _ver = ((_jv.loads((PROJ / "config.json").read_text(encoding="utf-8")).get("strategy") or {})
                .get("version") or "v1").lower()
        if _ver in ("v2", "v3") and len(all_locked):
            sys.path.insert(0, str(BASE / "Tse2" / "orderflow"))   # tsetmc.resolve for insCodes
            sys.path.insert(0, str(PROJ))
            import strategy_v3 as _sv
            _ranked = _sv.filter_scan(all_locked, PROJ / "config.json", DAILY, keep_all=True,
                                      log=lambda m: print(m, flush=True))
            print(f"strategy {_ver}: locked={len(all_locked)} -> eligible={len(_ranked)}")
            all_locked = _ranked.reset_index(drop=True)
    except Exception as exc:
        print(f"strategy filter skipped (file order kept): {str(exc)[:120]}")
    df = all_locked.copy()
    if len(all_locked) > MAX_PICK:
        # candidates 7..12 stay as the reserve used when an order never fills
        all_locked.iloc[MAX_PICK:MAX_PICK * 2][["symbol", "close", "next_ceiling", "turnover", "session"]].to_csv(
            PROJ / "state" / "reserve.csv", index=False, encoding="utf-8-sig")
    if len(df):
        # EXACT backtest behaviour: take the first signals in data-file order
        df = df.head(MAX_PICK).reset_index(drop=True)
        df["pick_rank"] = range(1, len(df) + 1)
        df["rank_score"] = range(MAX_PICK, MAX_PICK - len(df), -1)
        df["symbol_precision"] = TP
        df["symbol_signals"] = 0
    else:
        df = pd.DataFrame(columns=["symbol", "close", "ceiling", "next_ceiling", "dist_pct",
                                   "volume", "turnover", "session", "tp_pct", "sl_pct",
                                   "hold_days", "reason", "pick_rank", "rank_score"])
    df.to_csv(CAND, index=False, encoding="utf-8-sig")
    print(f"locked={len(rows)} picked={len(df)} -> candidates.csv")

    # nightly review: what did the bot actually submit today?
    try:
        import json as _j2
        log = PROJ / "state" / "orders_log.jsonl"
        rows = []
        if log.exists():
            rows = [_j2.loads(l) for l in log.read_text(encoding="utf-8").splitlines() if l.strip()][-10:]
        sub = sum(1 for r in rows if r.get("submitted"))
        print(f"orders_log: {len(rows)} attempts · {sub} submitted")
    except Exception as exc:
        print("orders_log error:", str(exc)[:60])

    # what happened to yesterday's orders? the panel is only reliable at night
    try:
        import sys as _s, json as _j
        _s.path.insert(0, str(PROJ / "src")); _s.path.insert(0, str(PROJ))
        import distbot as _bot
        power = _bot.cdp_power()
        open_syms = sorted(_bot.symbols_in_open_orders())
        _j.dump({"at": str(pd.Timestamp.now()), "power": power, "open_orders": open_syms},
                open(PROJ / "state" / "fills_note.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print(f"fills_note: power={power:,.0f} · open={open_syms}")
    except Exception as exc:
        print("fills_note error:", str(exc)[:80])

    # follow-up log
    try:
        log = df[["session", "symbol", "close", "next_ceiling", "tp_pct", "sl_pct", "reason"]].copy()
        log = log.rename(columns={"session": "تاریخ", "symbol": "نماد", "close": "قیمت بسته",
                                  "next_ceiling": "سقف فردا", "tp_pct": "حد سود",
                                  "sl_pct": "حد ضرر", "reason": "علت"})
        log.insert(2, "سیگنال", 1)
        if XLSX.exists():
            try:
                old = pd.read_excel(XLSX, sheet_name="signals")
                log = pd.concat([old, log], ignore_index=True).drop_duplicates(
                    subset=["تاریخ", "نماد"], keep="last")
            except Exception:
                pass
        log.to_excel(XLSX, sheet_name="signals", index=False)
    except Exception as exc:
        print("excel error:", str(exc)[:80])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
