"""Order today's candidates, unattended.

Called by the daemon at 08:45:01. For each candidate:
  * read the free balance (AccountBalance) from the broker
  * split it across the candidates (max 3)
  * hand the per-symbol budget to the Playwright flow, which reads the panel
    price, caps the quantity by «آستانه حجم», clicks buy and reports back.

Every attempt is appended to state/orders_log.jsonl for the nightly review.
"""
from __future__ import annotations

import pw_lock  # serialises Chrome on the shared profile

import os
import sys as _sys
try:
    _sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import json
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

BASE = Path(r"C:\Users\Administrator\Desktop\DeepSeek DeskTop\WarRobot")
sys.path.insert(0, str(BASE / "src"))
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE.parent / "Tse2" / "orderflow"))

import pandas as pd  # noqa: E402

CAND = BASE / "state" / "candidates.csv"
LOG = BASE / "state" / "orders_log.jsonl"
PW = BASE / "pw"
NODE = r"C:\Program Files\nodejs\node.exe"


def _clear_lock() -> None:
    prof = BASE / "state" / "pw-profile"
    for n in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
        try:
            (prof / n).unlink(missing_ok=True)
        except Exception:
            pass


def _run_node(script: str, timeout: int = 420) -> str:
    """Run a node script; on a locked profile reset it and retry once."""
    out = ""
    for attempt in range(2):
        try:
            p = subprocess.run([NODE, str(PW / script)], cwd=str(PW),
                               capture_output=True, text=True, timeout=timeout,
                               encoding="utf-8", errors="replace")
            out = (p.stdout or "") + (p.stderr or "")
        except Exception as exc:
            out = str(exc)
        if "existing browser session" not in out and out.strip():
            return out
        if attempt == 0:
            try:
                import pw_reset
                n = pw_reset.kill_orphans()
                print(f"[retry] profile_reset_retry killed {n} chrome", flush=True)
            except Exception:
                pass
    return out


def _snapshot_balance() -> float:
    """Balance saved by the morning/nightly snapshot (state/prices.json)."""
    try:
        import json as _json
        from pathlib import Path as _P
        d = _json.loads((_P(__file__).resolve().parent / "state" / "prices.json"
                         ).read_text(encoding="utf-8"))
        return float((d.get("balance") or {}).get("account_balance") or 0)
    except Exception:
        return 0.0


def _free_balance() -> float:
    """AccountBalance: live broker read (2 tries), then the snapshot fallback.

    A transient browser-lock conflict must never turn into a silent zero: that
    is exactly what skipped the pre-open order on 1405/08/23 and 08/25.
    """
    import time as _time
    for attempt in range(2):
        try:
            raw = _run_node("check2.js").strip()
            for line in reversed(raw.splitlines()):
                line = line.strip()
                if not line.startswith("{"):
                    continue
                data = json.loads(line)
                flat = json.dumps(data, ensure_ascii=False).replace("\\", "")
                m = re.search(r'"AccountBalance":([\d.]+)', flat)
                if m and float(m.group(1)) > 0:
                    return float(m.group(1))
                break
        except Exception:
            pass
        if attempt == 0:
            _time.sleep(8)
    snap = _snapshot_balance()
    try:
        with LOG.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                                 "symbol": None, "source": "snapshot_balance",
                                 "free_balance": snap, "submitted": False}) + "\n")
    except Exception:
        pass
    return snap



def _max_positions() -> int:
    try:
        cfg = json.loads((BASE / "config.json").read_text(encoding="utf-8"))
        return int(cfg["universe"].get("max_picks_per_day", 3))
    except Exception:
        return 3


def _live_close(symbol: str) -> float:
    """Latest TSETMC closing price — used to catch stale/nominal-changed files."""
    try:
        import tsetmc as api
        inst = api.resolve(symbol)
        if not inst:
            return 0.0
        raw = api.closing_daily(inst["ins_code"])
        rows = raw.get("closingPriceDaily") if isinstance(raw, dict) else None
        if not rows:
            return 0.0
        # TSETMC returns newest-first; sort by date so we never pick an old close
        rows = sorted(rows, key=lambda r: str(r.get("dEven") or ""))
        last = rows[-1]
        return float(last.get("pClosing") or last.get("pDrCotVal") or 0)
    except Exception:
        return 0.0


def _broker_holdings() -> dict:
    """Market value of each holding from the broker's advanced portfolio."""
    try:
        import json as _json
        from pathlib import Path as _P
        p = _P(__file__).resolve().parent / "state" / "pnl.json"
        d = _json.loads(p.read_text(encoding="utf-8"))
        return {str(r["symbol"]): float(r.get("value") or 0)
                for r in d.get("positions", []) if r.get("value")}
    except Exception:
        return {}


def _nightly_close() -> dict:
    """symbol -> final close from the nightly snapshot (state/prices.json)."""
    try:
        import json as _json
        from pathlib import Path as _P
        d = _json.loads((_P(__file__).resolve().parent / "state" / "prices.json"
                         ).read_text(encoding="utf-8"))
        return {str(k): float(v.get("close") or 0)
                for k, v in (d.get("symbols") or {}).items()}
    except Exception:
        return {}


def _open_orders() -> list[dict]:
    """Open orders straight from the broker (prevents double spending)."""
    try:
        for line in reversed(_run_node("broker_client.js").splitlines()):
            line = line.strip()
            if line.startswith("{"):
                data = json.loads(line)
                return data.get("orders", []) if isinstance(data, dict) else data
    except Exception:
        pass
    return []


def _broker_prices() -> dict:
    """Live panel prices from the last broker snapshot (authoritative intraday)."""
    try:
        import json as _json
        from pathlib import Path as _P
        d = _json.loads((_P(__file__).resolve().parent / "state" / "pnl.json"
                         ).read_text(encoding="utf-8"))
        if d.get("at", "")[:10] == __import__("datetime").date.today().isoformat()[:10] or True:
            return {str(r["symbol"]): float(r.get("price") or 0)
                    for r in d.get("positions", []) if r.get("price")}
    except Exception:
        pass
    return {}


def _close_is_fresh(date_int) -> bool:
    """A TSETMC close older than ~3 sessions must not be used for pricing."""
    try:
        ds = str(int(date_int))            # YYYYMMDD (Gregorian)
        from datetime import date
        d = date(int(ds[:4]), int(ds[4:6]), int(ds[6:8]))
        import market_cal
        return (market_cal.now_tehran().date() - d).days <= 5
    except Exception:
        return False                        # unknown date = do not trust it


def _close_is_fresh_by_px(symbol: str) -> bool:
    try:
        import tsetmc as _api
        rows = (_api.closing_daily(_api.resolve(symbol)["ins_code"]) or {}).get(
            "closingPriceDaily") or []
        last = sorted(rows, key=lambda r: str(r.get("dEven") or ""))[-1]
        return _close_is_fresh(last.get("dEven"))
    except Exception:
        return True


def _isin(symbol: str) -> str:
    try:
        import tsetmc as api
        inst = api.resolve(symbol)
        return str(inst.get("isin") or "") if inst else ""
    except Exception:
        return ""


BIND_FAILS = Path(__file__).resolve().parent / "state" / "bind_failures.json"


def _bind_failures() -> dict:
    try:
        return json.loads(BIND_FAILS.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _note_bind_failure(symbol: str) -> None:
    data = _bind_failures()
    rec = data.get(symbol) or {"count": 0, "last": ""}
    rec["count"] = int(rec.get("count") or 0) + 1
    rec["last"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    data[symbol] = rec
    try:
        BIND_FAILS.parent.mkdir(parents=True, exist_ok=True)
        BIND_FAILS.write_text(json.dumps(data, ensure_ascii=False, indent=1),
                              encoding="utf-8")
    except Exception:
        pass


def _bind_bad(symbol: str) -> bool:
    return int((_bind_failures().get(symbol) or {}).get("count") or 0) >= 3


def run(dry: bool = False) -> dict:
    out = {"at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "orders": []}
    if not CAND.exists():
        out["note"] = "no candidates file"
        return out
    df = pd.read_csv(CAND).head(_max_positions())
    if not len(df):
        out["note"] = "empty candidates"
        return out

    import pw_lock as _pl
    with _pl.guard("order_candidates") as got:
        if not got:
            out["note"] = "playwright_lock_timeout"
            return out

    free = _free_balance()
    out["free_balance"] = round(free, 0)
    open_orders = _open_orders()
    out["open_orders"] = [{"symbol": o.get("symbol"), "side": o.get("side"),
                           "qty": o.get("qty"), "executed": o.get("executed"),
                           "price": o.get("price")} for o in open_orders]
    # symbols that already have an open order are skipped, not the whole plan:
    # their reserved cash is subtracted so the next 1/3 slot can still be opened
    open_buy_syms = {str(o.get("symbol")) for o in open_orders
                     if "خرید" in str(o.get("side") or "")}
    reserved = 0.0
    for o in open_orders:
        try:
            reserved += float(o.get("price") or 0) * float(o.get("qty") or 0)
        except Exception:
            pass
    if open_buy_syms:
        out["open_buy_symbols"] = sorted(open_buy_syms)
        out["reserved_rial"] = round(reserved, 0)
    if free < 6_000_000:
        out["note"] = f"free balance too low ({free:,.0f})"
        return out

    # equal allocation: every symbol should end up at total_assets / 3.
    # We subtract what is already held in that symbol, so a top-up only adds the
    # missing part and the portfolio converges to an equal split.
    import market_cal as _mc  # noqa: F401  (kept for closed-day helpers)
    holdings = {}
    try:
        import portfolio_sync as _ps
        EXCLUDED_HOLDINGS = {"خساپا"}
        for pos in _ps.broker_positions():
            sym = str(pos["symbol"])
            if sym in EXCLUDED_HOLDINGS:
                continue
            qty = float(pos.get("qty") or 0)
            px = float(pos.get("avg") or 0)
            live_px = _live_close(sym)
            if live_px > 0:
                px = live_px          # market value wins over the broker average
            holdings[sym] = qty * px
    except Exception:
        holdings = {}
    # total assets from local authoritative data (no browser round trip)
    broker_values = _broker_holdings()
    if broker_values:
        total = free + sum(broker_values.values())
        holdings = dict(broker_values)
    else:
        total = free + sum(holdings.values())
        if total <= free:                     # nothing held -> last resort
            try:
                import rebalance as _reb
                total = max(total, _reb.total_assets())
            except Exception:
                pass
    target_each = (total / 3.0) if total > 0 else free / 3.0
    out["total_assets"] = round(total, 0)
    out["target_per_symbol"] = round(target_each, 0)
    out["equal_allocation"] = True
    # ---- holdings-first equal weight (holdings_first) --------------------
    MANUAL_ONLY = {"افران"}
    broker_held = _broker_holdings()
    if broker_held:
        holdings = {**holdings, **broker_held}   # broker value wins over TSETMC
    held = {sym: val for sym, val in holdings.items() if sym not in MANUAL_ONLY}
    slots_free = max(0, 3 - len(held))
    # never dilute a position that is already close to its take-profit
    try:
        import json as _json
        from pathlib import Path as _P
        _pnl_doc = _json.loads((_P(__file__).resolve().parent / "state" / "pnl.json"
                                ).read_text(encoding="utf-8"))
        near_tp = {str(x["symbol"]): float(x.get("pnl_pct") or 0)
                   for x in _pnl_doc.get("positions", [])
                   if float(x.get("pnl_pct") or 0) >= 20.0}
    except Exception:
        near_tp = {}
    if near_tp:
        out["skipped_near_tp"] = near_tp
    needs = {}
    for sym in held:
        if sym in near_tp:
            continue        # let the lot-based TP sell it instead of buying more
        if sym in locals().get("open_buy_syms", set()):
            continue        # an order for this symbol is already working
        needs[sym] = max(0.0, target_each - held[sym])
    # a new position only if a slot is free and the cash can carry a full target
    if slots_free > 0 and free >= target_each * 0.9:
        for _, r in df.iterrows():
            sym = str(r["symbol"])
            if sym in held or sym in MANUAL_ONLY or sym in needs:
                continue
            if _bind_bad(sym):
                out.setdefault("skipped_bind_bad", []).append(sym)
                continue
            if sym in locals().get("open_buy_syms", set()):
                continue
            needs[sym] = target_each
            slots_free -= 1
            if slots_free == 0:
                break
    total_need = sum(needs.values())
    cash_available = free * 0.95
    scale = min(1.0, cash_available / total_need) if total_need > 0 else 0.0
    out["allocation_scale"] = round(scale, 4)
    out["total_need"] = round(total_need, 0)
    out["planned_symbols"] = list(needs)
    needs = {k: v for k, v in needs.items() if v > 0}

    rows_by_symbol = {str(r["symbol"]): r for _, r in df.iterrows()}
    for sym in list(needs):
        r = rows_by_symbol.get(sym)
        if r is None:
            # a held symbol that is not in today's scan list: still top it up
            r = {"symbol": sym, "close": _live_close(sym), "isin": ""}
        isin = str(r.get("isin") or "") or _isin(sym)
        if not isin:
            out["orders"].append({"symbol": sym, "ok": False, "reason": "isin_not_found"})
            continue
        # price: the broker snapshot wins for holdings; otherwise the scan close
        try:
            px = float(r.get("close") or 0)
        except Exception:
            px = 0.0
        _bp = _broker_prices().get(sym, 0.0)
        if _bp > 0:
            px = _bp
        _np = _nightly_close().get(sym, 0.0)
        if _bp <= 0 and _np > 0:
            px = _np        # official close written by the 21:18 nightly scan
        # live price wins: the daily file can be 1-2 sessions stale
        live_px = _live_close(sym)
        if live_px > 0:
            # only trust it when the series is recent; otherwise let the panel price
            try:
                import tsetmc as _api
                _rows = (_api.closing_daily(_api.resolve(sym)["ins_code"]) or {}).get(
                    "closingPriceDaily") or []
                _last = sorted(_rows, key=lambda r: str(r.get("dEven") or ""))[-1]
                if not _close_is_fresh(_last.get("dEven")):
                    out.setdefault("warnings", []).append(
                        {"symbol": sym, "stale_close": live_px,
                         "last_date": _last.get("dEven")})
                    live_px = 0.0
            except Exception:
                pass
        if live_px > 0:
            px = live_px
        elif px and not _close_is_fresh_by_px(sym):
            px = 0.0  # stale (scan file too): flow.js uses the live panel price
        live = _live_close(sym)
        if live and px and abs(live / px - 1) > 0.5:
            out["orders"].append({"symbol": sym, "ok": False, "reason": "price_mismatch",
                                  "file": px, "live": live})
            continue
        # ---- pre-open price discovery ------------------------------------
        # the old chain used yesterday's close / panel prefill, so a symbol
        # locked at yesterday's ceiling was bid ~3% too low and never filled.
        price_source = "close"
        try:
            import price_discovery as _pd
            _prev = 0.0
            try:
                _prev = float(r.get("close") or 0)
            except Exception:
                _prev = 0.0
            if _prev <= 0:
                _prev = _nightly_close().get(sym, 0.0) or px
            _nc = 0.0
            try:
                _nc = float(r.get("next_ceiling") or 0)
            except Exception:
                _nc = 0.0
            _disc = _pd.buy_price(sym, _prev, _nc)
            if _disc["price"] > 0:
                px = float(_disc["price"])
                price_source = _disc["source"]
        except Exception as exc:
            out.setdefault("warnings", []).append(
                {"symbol": sym, "price_discovery_error": str(exc)[:80]})

        # equal allocation (proportional): this symbol's share of the free cash
        need_pre = needs.get(sym, 0.0)
        if px <= 0:
            # never fall back to a stale scan close; the panel prices the order
            px = _broker_prices().get(sym, 0.0)
        budget = need_pre * scale
        if px <= 0:
            # stale/unknown price: let flow.js size it from the live panel price
            qty = 0
        else:
            qty = int(budget / (px * 1.015))
        rec = {"at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
               "symbol": sym, "isin": isin, "budget": round(budget, 0),
               "price": px, "qty": qty, "price_source": price_source}
        if dry:
            rec["dry"] = True
            out["orders"].append(rec)
            continue
        try:
            p = subprocess.run([NODE, str(PW / "flow.js"), sym, isin, str(qty), str(px)],
                               cwd=str(PW), capture_output=True, text=True, timeout=900,
                               encoding="utf-8", errors="replace")
            data = {}
            for line in reversed((p.stdout or "").splitlines()):
                line = line.strip()
                if line.startswith("{"):
                    data = json.loads(line)
                    break
        except Exception as exc:
            data = {"message": f"error:{str(exc)[:60]}"}
        if "bind_mismatch" in str(data.get("message") or ""):
            _note_bind_failure(sym)
        if rec.get("submitted"):
            try:
                import trade_journal as _tj
                _tj.record_buy(sym, rec.get("qty"), rec.get("price") or px,
                               fee=0.0, reason=(r.get("reason") if isinstance(r, dict) else ""))
                rec["journaled"] = True
            except Exception as exc:
                rec["journal_error"] = str(exc)[:80]
        if not rec.get("submitted") and rec.get("qty"):
            for o in _open_orders():
                if str(o.get("symbol")) == sym and str(o.get("side")) == "خرید":
                    rec["submitted"] = True
                    rec["verified_by_open_orders"] = True
                    rec["orderid"] = o.get("orderid")
                    rec["price"] = float(o.get("price") or 0)
                    break
        rec.update({
            "qty": data.get("qty"),
            "price": data.get("finalPrice") or data.get("price"),
            "message": data.get("message"),
            "submitted": bool(re.search(r"موفق|ثبت", str(data.get("message") or ""))),
        })
        out["orders"].append(rec)
        with LOG.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    out["submitted"] = sum(1 for o in out["orders"] if o.get("submitted"))
    return out


if __name__ == "__main__":
    print(json.dumps(run(dry="--dry" in sys.argv), ensure_ascii=False, indent=1))
