"""TseBot_Dist — ربات مبتنی بر «فاصله تا سقف» (باند ۰٫۲۵٪).

Strategy
--------
1. nightly: for every symbol, ceiling = prev_close * (1+limit) and
   dist = (ceiling - close)/close*100. Keep only dist <= 0.25 (locked at ceiling).
2. 09:00   : confirm on the LIVE book (BestLimits): no seller at the ceiling and a
   buy queue worth >= min_queue_value_rial.
3. 09:01   : buy at the ceiling, sized from the live buying power and split by the
   broker's per-ticket limit (آستانه حجم).
4. daily   : sell at +12% / -5% / after 15 sessions (the band-0.25 backtest winner
   family), priced at the best bid, floored at the exchange floor when stopping.

The broker layer is imported from the archived TeseRobot project (it is proven:
tab-2 form, tick rounding, panel messages, CDP fallback).
"""
from __future__ import annotations

import json
import re
import math
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

try:                                    # Windows consoles default to cp1252
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = Path(__file__).resolve().parent      # .../TseBot_Dist/src
PROJ = HERE.parent                          # .../TseBot_Dist
BASE = PROJ.parent                          # .../DeepSeek DeskTop
LEGACY = BASE / "TeseRobot"
sys.path.insert(0, str(LEGACY / "src"))
sys.path.insert(0, str(BASE / "Tse2" / "orderflow"))

import jdatetime                                    # noqa: E402
from tsebot.alerts import send, alert_order          # noqa: E402
from tsebot.broker_selenium import SeleniumBroker    # noqa: E402
from tsebot.calendar_ir import now_tehran            # noqa: E402
from tsebot.ledger import Ledger                     # noqa: E402
import tsetmc as api                                 # noqa: E402

MIN_ORDER_VALUE_RIAL = 5_000_000
_LOGIN_ALERTED = 0.0          # throttle for the session-expired email
MAXPOS = 3                       # 1/3 of the cash per position  # بورس: حداقل ارزش سفارش خرید
CONFIG = json.loads((PROJ / "config.json").read_text(encoding="utf-8"))
DAILY = LEGACY / "data" / "daily"
STATE = PROJ / "state"
STATE.mkdir(exist_ok=True)
LOG = PROJ / "bot.log"


def log(msg: str) -> None:
    line = f"[{now_tehran().strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    try:
        print(line, flush=True)
    except Exception:
        print(line.encode("utf-8", "replace").decode("ascii", "replace"), flush=True)
    with LOG.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def ledger() -> Ledger:
    return Ledger(STATE / "ledger.jsonl")


# --------------------------------------------------------------------- scanning
def ceiling_for(prev_close: float, limit: float = 3.0) -> float:
    return float(round(prev_close * (1 + limit / 100.0)))


def scan_nightly() -> pd.DataFrame:
    """Every symbol whose close sits inside the 0.25% band of its ceiling."""
    max_dist = float(CONFIG["band"]["max_dist_to_ceiling_pct"])
    lasts, rows = {}, []
    for f in DAILY.glob("*_daily.csv"):
        try:
            d = pd.read_csv(f, dtype={"date": str})
        except Exception:
            continue
        if len(d) < 25:
            continue
        lasts[f.name[:-len("_daily.csv")]] = str(d["date"].iloc[-1])
    if not lasts:
        return pd.DataFrame()
    market_last = max(lasts.values())
    for f in DAILY.glob("*_daily.csv"):
        sym = f.name[:-len("_daily.csv")]
        try:
            d = pd.read_csv(f, dtype={"date": str})
        except Exception:
            continue
        if len(d) < 25 or str(d["date"].iloc[-1]) != market_last:
            continue
        d = d.sort_values("date").reset_index(drop=True)
        prev, close = float(d["Close"].iloc[-2]), float(d["Close"].iloc[-1])
        vol = float(d["Volume"].iloc[-1])
        ceil = ceiling_for(prev)
        dist = (ceil - close) / close * 100
        if dist <= max_dist:
            rows.append({"symbol": sym, "close": close, "ceiling": ceil,
                         "next_ceiling": ceiling_for(close),
                         "dist_pct": round(dist, 3), "volume": vol,
                         "turnover": vol * close, "session": market_last})
    out = pd.DataFrame(rows).sort_values("turnover", ascending=False) if rows else pd.DataFrame()
    try:  # strategy switch: v2 entry filters (regime, liquidity, floor-lock history); v1 = no-op
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        import strategy_v2 as _s2
        out = _s2.filter_scan(out, Path(__file__).resolve().parent.parent / "config.json", DAILY)
    except Exception as exc:
        log(f"strategy_v2 filter skipped: {str(exc)[:80]}")
    prof = PROJ / "state" / "symbol_profiles.csv"
    if len(out) and prof.exists():
        try:
            p = pd.read_csv(prof)
            keep = [c for c in ("symbol", "close_pos_median", "lock_freq_pct",
                                "p_lock_rsi60plus", "p_lock_macd_pos", "p_lock_bb_top")
                    if c in p.columns]
            out = out.merge(p[keep], on="symbol", how="left")
        except Exception:
            pass
    return out


def live_confirm(cands: pd.DataFrame, workers: int = 10) -> pd.DataFrame:
    """Keep the ones that are really locked now with a big enough buy queue."""
    from concurrent.futures import ThreadPoolExecutor
    min_q = float(CONFIG["band"]["min_queue_value_rial"])

    def probe(r):
        inst = api.resolve(r["symbol"])
        if not inst:
            return None
        b = api.parse_book(api.best_limits(inst["ins_code"]))
        bid, ask, vol = b["best_bid"], b["best_ask"], b["bid_volume_5"]
        if not bid or ask or not vol:
            return None
        if abs(bid - float(r["ceiling"])) > max(1.0, float(r["ceiling"]) * 0.0025):
            return None
        value = vol * bid
        if value < min_q:
            return None
        return {**r.to_dict(), "live_bid": bid, "queue_value": value}

    out = []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for r in ex.map(probe, [row for _, row in cands.iterrows()]):
            if r:
                out.append(r)
    return pd.DataFrame(out).sort_values("queue_value", ascending=False) if out else pd.DataFrame()




def big_small_ratio(symbol: str) -> float:
    """Big/small volume ratio from LAST NIGHT's tape (state/big_small.json).

    Computed by big_small_nightly.py right after the nightly download, so at 09:00
    there is no API call and no look-ahead into the session we are buying.
    """
    try:
        cache = json.loads((PROJ / "state" / "big_small.json").read_text(encoding="utf-8"))
        return float(cache.get(symbol, {}).get("ratio") or 0.0)
    except Exception:
        return 0.0


def learned_weights() -> dict:
    try:
        return json.loads((PROJ / "state" / "weights.json").read_text(encoding="utf-8")).get("weights", {})
    except Exception:
        return {}


def score_of(row: dict, w: dict) -> float:
    """queue value weighted by what the tracker says pays."""
    dist = float(row.get("dist_pct") or 0)
    bs = float(row.get("big_to_small") or 0)
    bd = "dist_0_005" if dist <= 0.05 else ("dist_005_015" if dist <= 0.15 else "dist_015_025")
    bb = "bs_lt1" if bs < 1 else ("bs_1_3" if bs < 3 else "bs_3p")
    return float(row.get("queue_value") or 0) * w.get(bd, 1.0) * w.get(bb, 1.0)


def live_scan_full(workers: int = 8, scope: str = "locked",
                   budget_sec: float = 25.0) -> pd.DataFrame:
    """09:00 full-market scan on LIVE data (no nightly list needed).

    For every symbol: ceiling = yesterday's close * (1+limit); the book is locked
    when the best bid equals that ceiling and nobody sells at the ceiling. The buy
    queue must be worth at least ``min_queue_value_rial``. Ranking: queue value.
    """
    from concurrent.futures import ThreadPoolExecutor
    max_dist = float(CONFIG["band"]["max_dist_to_ceiling_pct"])
    min_q = float(CONFIG["band"]["min_queue_value_rial"])

    # yesterday's close per symbol + which ones were locked (scope filter)
    prev_close, locked_yday = {}, []
    for f in DAILY.glob("*_daily.csv"):
        sym = f.name[:-len("_daily.csv")]
        try:
            d = pd.read_csv(f, usecols=["date", "Close"], dtype={"date": str})
        except Exception:
            continue
        if len(d) < 2:
            continue
        prev = float(d["Close"].iloc[-1])
        prev_close[sym] = prev
        # was the last session locked? ceiling from the close two days back
        if len(d) >= 3:
            c2 = float(d["Close"].iloc[-2])
            if prev >= ceiling_for(c2) * 0.999:
                locked_yday.append(sym)
    if scope == "locked" and locked_yday:
        prev_close = {k: v for k, v in prev_close.items() if k in set(locked_yday)}

    # priority: the most traded names first, so a 25 s budget covers the ones
    # that can actually be bought in size
    turnover = {}
    for f in DAILY.glob("*_daily.csv"):
        try:
            d = pd.read_csv(f, usecols=["Volume", "Close"])
        except Exception:
            continue
        if len(d):
            turnover[f.name[:-len("_daily.csv")]] = float(d["Volume"].iloc[-1] * d["Close"].iloc[-1])
    items = sorted(prev_close.items(), key=lambda kv: turnover.get(kv[0], 0), reverse=True)
    deadline = time.time() + float(budget_sec)

    def probe(item):
        if time.time() > deadline:
            return None
        sym, prev = item
        inst = api.resolve(sym)
        if not inst:
            return None
        b = api.parse_book(api.best_limits(inst["ins_code"]))
        bid, ask, vol = b["best_bid"], b["best_ask"], b["bid_volume_5"]
        if not bid or not vol:
            return None
        ceil = ceiling_for(prev)
        dist = (ceil - bid) / bid * 100
        if dist > max_dist or (ask and ask > 0):
            return None
        value = vol * bid
        if value < min_q:
            return None
        ratio = big_small_ratio(sym)
        need = float(CONFIG["band"].get("min_big_to_small_ratio", 0) or 0)
        if need and ratio < need:
            return None
        return {"symbol": sym, "ceiling": ceil, "live_bid": bid, "ask": ask,
                "big_to_small": round(ratio, 2),
                "dist_pct": round(dist, 3), "queue_qty": vol, "queue_value": value}

    out = []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for r in ex.map(probe, items):
            if r:
                out.append(r)
    if not out:
        return pd.DataFrame()
    w = learned_weights()
    df_out = pd.DataFrame(out)
    df_out["score"] = [score_of(r, w) for r in out]
    return (df_out
            .sort_values("score", ascending=False)
            .head(int(CONFIG["universe"]["max_picks_per_day"])))


# ------------------------------------------------------------------------ buy
def broker() -> SeleniumBroker:
    b = SeleniumBroker(chromedriver=CONFIG["broker"]["chromedriver"],
                       debugger_address=CONFIG["broker"]["debugger_address"])
    b.connect()
    return b


def symbols_in_open_orders() -> set:
    """Symbols that already have a live order in the panel — a second guard.

    The ledger can miss a fill (it has been corrupted before), so the panel is
    read as well before placing anything.
    """
    js = """(function(){
    var out=[];
    document.querySelectorAll('ul.g-row').forEach(function(r){
      var t=(r.innerText||'').replace(/\s+/g,' ');
      if(t.indexOf('خرید')<0 && t.indexOf('فروش')<0) return;
      var li=r.querySelectorAll('li');
      for(var i=0;i<li.length;i++){
        var v=(li[i].innerText||'').trim();
        if(v && v.length<=12 && !/[0-9,]{3,}/.test(v) && /^[\u0600-\u06FF0-9 ]+$/.test(v)){ out.push(v); break; }
      }
    });
    return JSON.stringify(out.slice(0,20));})()"""
    try:
        sys.path.insert(0, str(LEGACY / "src"))
        from tsebot.cdp import CDP
        c = CDP()
        try:
            if "/Login" in c.url:
                return set()
            raw = c.eval(js) or "[]"
            import json as _j
            return {str(x).strip() for x in _j.loads(raw) if str(x).strip()}
        finally:
            try:
                c.close()
            except Exception:
                pass
    except Exception:
        return set()


def bought_today() -> set:
    """Symbols already bought in the current Tehran session (from the ledger)."""
    import json as _j
    from datetime import datetime as _dt, timedelta as _td
    today = (now_tehran() - _td(minutes=0)).strftime("%Y-%m-%d")
    out = set()
    fp = PROJ / "state" / "ledger.jsonl"
    if not fp.exists():
        return out
    for line in fp.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or '"buy"' not in line:
            continue
        try:
            r = _j.loads(line)
        except Exception:
            continue
        if r.get("side") != "buy":
            continue
        ts = str(r.get("ts") or "")
        if ts[:10] == today:
            out.add(str(r.get("symbol")))
    return out


def cdp_power() -> float:
    """Buying power from the panel balance cell (no Selenium)."""
    import re as _re
    js = """(function(){
    var el=document.querySelector('.customerBalance_Account');
    if(el && el.innerText) return el.innerText;
    var out='';
    document.querySelectorAll('*').forEach(function(x){
      if(out || x.children.length) return;
      if((x.innerText||'').trim()!=='قدرت خرید') return;
      var p=x.parentElement;
      if(p && /[0-9]{6,}/.test(p.innerText||'')) out=p.innerText;
    });
    return out;})()"""
    try:
        sys.path.insert(0, str(LEGACY / "src"))
        from tsebot.cdp import CDP
        c = CDP()
        try:
            if "/Login" in c.url:
                return 0.0
            txt = c.eval(js) or ""
            m = _re.search(r"([\d,]{6,})", txt)
            return float(m.group(1).replace(",", "")) if m else 0.0
        finally:
            try:
                c.close()
            except Exception:
                pass
    except Exception:
        return 0.0


def buy(symbols: pd.DataFrame, dry: bool = False) -> list:
    led = ledger()
    results = []
    b = broker() if dry else None          # Selenium is only needed for a dry run
    power = float(b.buying_power() or 0) if dry else cdp_power()
    power0 = power                          # each position is sized on this snapshot
    fee = 1 + float(CONFIG["fees"]["buy_pct"]) / 100
    already = bought_today() | symbols_in_open_orders()
    for _, r in symbols.iterrows():
        sym = r["symbol"]
        if sym in already:
            results.append({"symbol": sym, "ok": False, "why": "already_bought_today"})
            continue
        price = float(r["next_ceiling"]) if "next_ceiling" in r and pd.notna(r.get("next_ceiling")) else float(r["ceiling"])
        qmax = 0                                        # read later by submit_via_cdp
        qty = int((power0 / MAXPOS) / (price * fee))
        min_qty = int(-(-MIN_ORDER_VALUE_RIAL // int(price)))   # ceil(5,000,000 / price)
        if qty < min_qty:
            results.append({"symbol": sym, "ok": False, "why": "below_min_value",
                            "need": min_qty * price})
            continue
        if qmax:
            qty = min(qty, qmax)
        if qty <= 0:
            results.append({"symbol": sym, "ok": False, "why": "no_power"})
            continue
        log(f"buy {sym}: {qty:,} @ {price:,.0f} (power {power:,.0f})")
        if dry:
            results.append({"symbol": sym, "ok": True, "dry": True, "qty": qty, "price": price})
            continue
        try:
            # live price from the panel: the file value goes stale overnight and the
            # exchange rejects anything outside today's band
            res = submit_via_cdp("buy", sym, qty, None)
            live = float(res.get("price") or 0)
            if live and price and abs(live / price - 1) > 0.20:
                log(f"skip {sym}: live {live:,.0f} vs file {price:,.0f} (nominal change?)")
                results.append({"symbol": sym, "ok": False, "why": "price_mismatch",
                                "live": live, "file": price})
                continue
        except Exception as exc:
            log(f"cdp buy error {sym}: {exc}")
            res = {"ok": False, "reason": "cdp_error"}
        if not res.get("ok"):
            try:
                res2 = b.submit_tab2("buy", sym, qty, price, None)
                res = res2 if res2.get("ok") else res
            except Exception:
                pass
        ok = bool(res.get("ok"))
        msg_txt = str(res.get("message") or res.get("reason") or "")
        if "orderstatev6_2004" in msg_txt or "قدرت خرید" in msg_txt or "موجودی" in msg_txt:
            # 2004 = not enough buying power (confirmed 2026-10-26): stop the whole
            # batch, every further ticket would fail the same way.
            log(f"NO POWER ({msg_txt[:60]}) — aborting the buy batch")
            results.append({"symbol": sym, "ok": False, "why": "no_power", "message": msg_txt})
            try:
                alert_order(sym, "buy", qty, price, "رد شد", "قدرت خرید کافی نیست")
            except Exception:
                pass
            break
        log(f"submit {sym}: {str(res.get('message') or res.get('reason'))[:120]}")
        if ok:
            fee_rial = qty * price * float(CONFIG["fees"]["buy_pct"]) / 100
            try:
                save_target(sym, float(r.get("tp_pct") or 15.0), float(r.get("sl_pct") or 4.0),
                            int(r.get("hold_days") or 15))
            except Exception:
                pass
            led.record_fill(symbol=sym, side="buy", qty=qty, price=price,
                            fee=fee_rial, order_no=f"dist-{now_tehran():%Y%m%d%H%M%S}",
                            source="broker", ts=None)
            power -= qty * price * fee
        try:
            alert_order(sym, "buy", qty, price, "ثبت شد" if ok else "رد شد",
                        str(res.get("message") or res.get("reason") or "")[:200])
        except Exception:
            pass
        results.append({"symbol": sym, "ok": ok, "qty": qty, "price": price,
                        "result": res.get("message") or res.get("reason")})
    return results


# ----------------------------------------------------------------------- sell
def sell_loop(dry: bool = False) -> dict:
    s = CONFIG["sell"]
    led = ledger()
    out = {"decisions": [], "executed": []}
    b = None                                # CDP only: Selenium is unusable here
    today = now_tehran().strftime("%Y-%m-%d")
    for pos in led.open_positions():
        if pos.symbol in CONFIG["universe"]["exclude_symbols"]:
            continue
        # a position bought today is not managed today: the recorded price is the
        # order price, not the fill, so a stop would fire on a phantom loss
        if str(getattr(pos, "first_ts", ""))[:10] == today:
            out["decisions"].append({"symbol": pos.symbol, "reason": "fresh_today_skip"})
            continue
        # live quote from TSETMC: the broker object has no market_quote()
        last = 0.0
        try:
            inst = api.resolve(pos.symbol)
            if inst:
                book = api.parse_book(api.best_limits(inst["ins_code"]))
                last = float(book.get("best_bid") or book.get("mid") or 0) or 0.0
        except Exception:
            last = 0.0
        if not last:
            try:                     # panel price (live) before the daily close
                import broker_portfolio as _bp
                _p = _bp.last_prices().get(pos.symbol)
                if _p:
                    last = float(_p)
            except Exception:
                pass
        if not last:
            try:                     # market closed -> use the last daily close
                df = pd.read_csv(DAILY / f"{pos.symbol}_daily.csv", dtype={"date": str})
                last = float(df.sort_values("date")["Close"].iloc[-1])
            except Exception:
                pass
        if not last:
            log(f"sell skip {pos.symbol}: no quote")
            continue
        log(f"sell check {pos.symbol}: last={last:,.0f}")
        pnl = (last - pos.avg_cost) / pos.avg_cost * 100 if pos.avg_cost else 0
        days = 0
        try:
            days = (now_tehran().date() - jdatetime.date.fromgregorian(
                date=datetime.fromisoformat(str(pos.first_ts)[:19]).date()).togregorian()).days
        except Exception:
            days = 0
        if pos.symbol in forced_symbols():
            last_px = float(last)
            log(f"FORCE SELL {pos.symbol} at {last_px:,.0f}")
            if not dry:
                b.bind_symbol(pos.symbol)
                _lo, qmax = b.order_limits()
                left = int(pos.qty)
                ok_all = True
                while left > 0:
                    n = min(left, qmax) if qmax else left
                    try:
                        res = submit_via_cdp("sell", pos.symbol, n, None)
                    except Exception as exc:
                        log(f"cdp sell error {pos.symbol}: {exc}")
                        res = {"ok": False, "reason": "cdp_error"}
                    if not res.get("ok"):
                        try:
                            res = b.submit_tab2("sell", pos.symbol, n, last_px, None)
                        except Exception:
                            pass
                    if not res.get("ok"):
                        ok_all = False
                        log(f"FORCE SELL failed {pos.symbol}: {str(res.get('message') or res.get('reason'))[:80]}")
                        break
                    led.record_fill(symbol=pos.symbol, side="sell", qty=n, price=last_px,
                                    fee=n * last_px * float(CONFIG["fees"]["sell_pct"]) / 100,
                                    order_no=f"force-{now_tehran():%Y%m%d%H%M%S}", source="broker", ts=None)
                    left -= n
                if ok_all:
                    try:
                        data = json.loads(FORCE_SELL.read_text(encoding="utf-8"))
                        data["symbols"] = [x for x in data.get("symbols", []) if x != pos.symbol]
                        FORCE_SELL.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
                    except Exception:
                        pass
            out["decisions"].append({"symbol": pos.symbol, "reason": "force", "pnl_pct": round(pnl, 2), "price": last_px})
            continue
        tgt = target_of(pos.symbol)
        tp = float(tgt.get("tp") or s["target_pct"])
        sl = float(tgt.get("sl") or s["stop_pct"])
        hold = int(tgt.get("hold") or s["max_hold_days"])
        reason = None
        if pnl >= tp:
            reason = "target"
        elif pnl <= -sl:
            reason = "stop"
        elif days >= hold:
            reason = "time"
        if not reason:
            continue
        price = float(last)
        if reason == "stop":
            try:
                price = float(round(pos.avg_cost * (1 - sl / 100), 0))
            except Exception:
                price = float(last)
        out["decisions"].append({"symbol": pos.symbol, "reason": reason,
                                 "pnl_pct": round(pnl, 2), "price": price})
        if dry:
            continue
        b.bind_symbol(pos.symbol)
        _lo, qmax = b.order_limits()
        left = int(pos.qty)
        while left > 0:
            n = min(left, qmax) if qmax else left
            try:
                res = submit_via_cdp("sell", pos.symbol, n, price)
            except Exception:
                res = {"ok": False}
            if not res.get("ok"):
                res = b.submit_tab2("sell", pos.symbol, n, price, None)
            out["executed"].append({"symbol": pos.symbol, "qty": n, "ok": bool(res.get("ok")),
                                    "msg": str(res.get("message") or res.get("reason"))[:120]})
            if not res.get("ok"):
                break
            led.record_fill(symbol=pos.symbol, side="sell", qty=n, price=price,
                            fee=n * price * float(CONFIG["fees"]["sell_pct"]) / 100,
                            order_no=f"dist-sell-{now_tehran():%H%M%S}", source="broker", ts=None)
            left -= n
    return out


def cancel_open(symbols=None) -> int:
    """Cancel open orders (all or per symbol) through the broker panel."""
    try:
        b = broker()
        n = b.cancel_open_orders(symbols)
        log(f"cancel_open {symbols or 'all'} -> {n} order(s)")
        return int(n or 0)
    except Exception as exc:
        log(f"cancel_open error: {exc}")
        return 0


def session_state() -> str:
    """'ok' | 'logged_out' | 'unknown' — checked through CDP (Selenium is dead here)."""
    try:
        sys.path.insert(0, str(LEGACY / "src"))
        from tsebot.cdp import CDP
        c = CDP()
        try:
            url = str(c.url or "")
            if "meibours" not in url:
                return "unknown"
            if "login" in url.lower():
                return "logged_out"
            has_panel = c.eval("(function(){return !!document.querySelector('#send_order_txtCount') "
                               "|| !!document.querySelector('#txt_search') "
                               "|| (document.body.innerText||'').indexOf('دارایی لحظه')>=0;})()")
            return "ok" if has_panel else "unknown"
        finally:
            try:
                c.close()
            except Exception:
                pass
    except Exception:
        return "unknown"


TARGETS = PROJ / "state" / "targets.json"


def save_target(symbol: str, tp: float, sl: float, hold: int) -> None:
    """Remember the per-symbol exit levels used at buy time."""
    try:
        data = json.loads(TARGETS.read_text(encoding="utf-8")) if TARGETS.exists() else {}
    except Exception:
        data = {}
    data[symbol] = {"tp": float(tp), "sl": float(sl), "hold": int(hold),
                    "set_at": now_tehran().strftime("%Y-%m-%d %H:%M")}
    TARGETS.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def target_of(symbol: str) -> dict:
    try:
        data = json.loads(TARGETS.read_text(encoding="utf-8"))
        return data.get(symbol) or {}
    except Exception:
        return {}


FORCE_SELL = PROJ / "state" / "force_sell.json"


def forced_symbols() -> set:
    try:
        return set(json.loads(FORCE_SELL.read_text(encoding="utf-8")).get("symbols", []))
    except Exception:
        return set()


def submit_via_cdp(side: str, symbol: str, qty: int, price: float | None = None) -> dict:
    """Place an order the way the operator does it, verified end to end.

    Never navigate: a full page load drops the broker session. Everything happens
    inside the open SPA tab:
        1) click the «دیده بان» stack so the symbol box gets a width
        2) click the box, type the symbol, fire key events (angucomplete only
           reacts to keys, not to a plain value change)
        3) click the exact result -> the panel binds that symbol
        4) read «آستانه حجم» -> the ticket is capped by it
        5) price: sell -> first bid of «پنج مظنه برتر», buy -> first ask
        6) fill count/price, click send, read the panel message
    """
    import json as _j
    import re as _re
    import time as _t
    sys.path.insert(0, str(LEGACY / "src"))
    from tsebot.cdp import CDP

    c = CDP()
    try:
        if "/Login" in c.url:
            global _LOGIN_ALERTED
            if _t.time() - _LOGIN_ALERTED > 1800:
                _LOGIN_ALERTED = _t.time()
                try:
                    from tsebot.alerts import send as _send
                    _send("[WarRobot] نشست کارگزاری قطع است",
                          "صفحه روی Login است؛ سفارشی ثبت نمیشود.", key="login-alert", force=True)
                except Exception:
                    pass
            return {"ok": False, "reason": "login_screen"}

        def _w() -> int:
            return c.eval("""(function(){var e=document.querySelector('#txt_search');
            return e?Math.round(e.getBoundingClientRect().width):0;})()""") or 0

        # 1) open the watchlist stack until the search box has a width
        for _ in range(3):
            if _w() > 50:
                break
            c.eval("""(function(){var el=null;
            document.querySelectorAll('span.lm_title').forEach(function(x){
              if((x.innerText||'').trim()==='دیده بان') el=x;});
            if(!el) return 0; var t=el.closest('a,div,li')||el;
            ['mousedown','mouseup','click'].forEach(function(e){t.dispatchEvent(new MouseEvent(e,{bubbles:true,cancelable:true,view:window}));});
            return 1;})()""")
            _t.sleep(2.5)
        if _w() <= 50:
            return {"ok": False, "reason": "search_box_closed"}

        # 2) type and make angucomplete react
        pos = c.eval("""(function(){var e=document.querySelector('#txt_search'); var b=e.getBoundingClientRect();
        return JSON.stringify({x:b.x+b.width/2,y:b.y+b.height/2});})()""")
        p0 = _j.loads(pos)
        for t in ("mousePressed", "mouseReleased"):
            c.call("Input.dispatchMouseEvent", {"type": t, "x": p0["x"], "y": p0["y"],
                                                "button": "left", "clickCount": 1})
        _t.sleep(0.4)
        c.eval("""(function(){var e=document.querySelector('#txt_search'); if(e){e.value='';e.focus();} return 1;})()""")
        c.call("Input.insertText", {"text": symbol})
        _t.sleep(1.5)
        c.eval("""(function(){var e=document.querySelector('#txt_search'); if(!e) return 0; e.focus();
        ['keydown','keypress','keyup'].forEach(function(k){
          e.dispatchEvent(new KeyboardEvent(k,{key:'a',bubbles:true}));});
        e.dispatchEvent(new Event('input',{bubbles:true})); return 1;})()""")
        _t.sleep(2)

        # 3) click the exact result
        clicked = c.eval("""(function(sym){
        var cands=[];
        document.querySelectorAll('div,li,span').forEach(function(x){
          var t=(x.innerText||'').trim();
          if(t===sym && x.offsetParent!==null) cands.push(x);});
        if(!cands.length) return 0;
        var tgt=cands[0].closest('li,a,div')||cands[0];
        ['mousedown','mouseup','click'].forEach(function(e){tgt.dispatchEvent(new MouseEvent(e,{bubbles:true,cancelable:true,view:window}));});
        return 1;})(""" + _j.dumps(symbol, ensure_ascii=False) + """)""")
        if not clicked:                       # one slower retry: the list can lag
            _t.sleep(2.5)
            c.eval("""(function(){var e=document.querySelector('#txt_search');
            if(e){e.focus(); e.value='';} return 1;})()""")
            c.call("Input.insertText", {"text": symbol})
            _t.sleep(1.5)
            c.eval("""(function(){var e=document.querySelector('#txt_search'); if(!e) return 0; e.focus();
            ['keydown','keypress','keyup'].forEach(function(k){
              e.dispatchEvent(new KeyboardEvent(k,{key:'a',bubbles:true}));});
            e.dispatchEvent(new Event('input',{bubbles:true})); return 1;})()""")
            _t.sleep(2)
            clicked = c.eval("""(function(sym){
            var cands=[];
            document.querySelectorAll('div,li,span').forEach(function(x){
              var t=(x.innerText||'').trim();
              if(t===sym && x.offsetParent!==null) cands.push(x);});
            if(!cands.length) return 0;
            var tgt=cands[0].closest('li,a,div')||cands[0];
            ['mousedown','mouseup','click'].forEach(function(e){tgt.dispatchEvent(new MouseEvent(e,{bubbles:true,cancelable:true,view:window}));});
            return 1;})(""" + _j.dumps(symbol, ensure_ascii=False) + """)""")
            _t.sleep(2.5)
        if not clicked:
            return {"ok": False, "reason": "symbol_result_not_found"}
        _t.sleep(2.5)
        bound = c.eval("""(function(){var e=document.querySelector('#fulltextContainer');
        return e?(e.innerText||'').trim():'';})()""")
        if not bound or not str(bound).startswith(str(symbol)[:2]):
            return {"ok": False, "reason": f"not_bound:{str(bound)[:14]}"}

        # 4) side + limits + book
        sel = ".orderside65" if str(side).lower() == "buy" else ".orderside86"
        c.eval("""(function(){var e=document.querySelector('%s'); if(e) e.click(); return 1;})()""" % sel)
        _t.sleep(1)
        txt = c.eval("(function(){return (document.body.innerText||'').replace(/\\s+/g,' ');})()") or ""
        m = _re.search(r"آستانه\s*حجم[^\d]{0,6}([\d,]+)\s*[-–]\s*([\d,]+)", txt)
        hi = int(m.group(2).replace(",", "")) if m else 0
        book = _re.search(r"تعداد حجم قیمت قیمت حجم تعداد\s*([\d,]+)\s+([\d,]+)\s+([\d,]+)\s+([\d,]+)\s+([\d,]+)\s+([\d,]+)", txt)
        bid = ask = 0
        if book:
            bid = int(book.group(3).replace(",", ""))
            ask = int(book.group(4).replace(",", ""))
        if hi:
            qty = min(int(qty), hi)
        if price is None:
            price = bid if str(side).lower() == "sell" else (ask or bid)
        price = float(price or 0)
        if qty <= 0 or price <= 0:
            return {"ok": False, "reason": f"bad_ticket qty={qty} price={price} limit={hi} bid={bid} ask={ask}",
                    "price": price, "limit": hi, "bid": bid, "ask": ask, "qty": qty}

        # 5) fill, send, read the message
        c.eval("""(function(){
        function setv(sel,v){var e=document.querySelector(sel); if(!e) return false;
          e.focus(); e.value=v; e.dispatchEvent(new Event('input',{bubbles:true}));
          e.dispatchEvent(new Event('change',{bubbles:true})); return true;}
        setv('#send_order_txtCount','%d'); setv('#send_order_txtPrice','%d'); return 1;})()"""
               % (int(qty), int(price)))
        _t.sleep(0.6)
        btn = c.eval("""(function(){var a=document.getElementById('send_order_btnBuySell'); if(!a) return '';
        a.scrollIntoView({block:'center'}); var b=a.getBoundingClientRect();
        return JSON.stringify({x:b.x+b.width/2,y:b.y+b.height/2});})()""")
        if not btn:
            return {"ok": False, "reason": "no_send_button"}
        pb = _j.loads(btn)
        for t in ("mousePressed", "mouseReleased"):
            c.call("Input.dispatchMouseEvent", {"type": t, "x": pb["x"], "y": pb["y"],
                                                "button": "left", "clickCount": 1})
        _t.sleep(4)
        msg = c.eval("""(function(){var out='';
        document.querySelectorAll('div,span,p').forEach(function(x){
          var t=(x.innerText||'').trim();
          var cls=((x.className||'')+'').toString();
          if(/symbol-state|SymbolState/i.test(cls)) return;          // «مجاز» وضعیت نماد است، نه پیام
          if(t && t.length<120 && /موفق|عدم پذیرش|خطا|کافی|انجام شد|ثبت شد/.test(t) && !/^مجاز$/.test(t) && x.children.length===0) out=t;});
        return out;})()""")
        ok = bool(msg and ("موفق" in msg or "انجام شد" in msg))
        return {"ok": ok, "message": msg or "", "qty": qty, "price": price,
                "limit": hi, "bid": bid, "ask": ask}
    finally:
        try:
            c.close()
        except Exception:
            pass


_LAST_PING = 0.0


def keep_alive(every_sec: int = 150) -> str:
    """Warm the broker session so it does not idle out.

    A same-origin fetch is enough; the page itself is never reloaded (a reload
    drops the session).
    """
    global _LAST_PING
    import time as _t
    if _t.time() - _LAST_PING < every_sec:
        return "skip"
    _LAST_PING = _t.time()
    sys.path.insert(0, str(LEGACY / "src"))
    try:
        from tsebot.cdp import CDP
        c = CDP()
        try:
            if "/Login" in c.url:
                return "login"
            c.eval("(function(){try{fetch('/Home/Default/page-1',{credentials:'include'});"
                   "fetch('/Home/OrderList',{credentials:'include'});}catch(e){} return 1;})()")
            return "ok"
        finally:
            try:
                c.close()
            except Exception:
                pass
    except Exception as exc:
        return f"error:{str(exc)[:40]}"


def ensure_session() -> str:
    """Log back in automatically when the broker page shows the login screen."""
    try:
        sys.path.insert(0, str(PROJ / "ocr"))
        import importlib
        import login as ocr_login
        importlib.reload(ocr_login)
        r = ocr_login.ensure_login()
        if r.get("ok"):
            log(f"auto-login ok (captcha {r.get('captcha','-')})")
            return "ok"
        log(f"auto-login failed: {r.get('reason')}")
        return str(r.get("reason") or "failed")
    except Exception as exc:
        log(f"auto-login error: {str(exc)[:80]}")
        return "error"
