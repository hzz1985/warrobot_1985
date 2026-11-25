"""One take-profit / stop-loss decision + execution path (Hark fix, bug #3).

Used by intraday_exit.py (09:00-12:30, every few minutes) and by
morning_orders.py (pre-open sell orders queued at 08:45:01).

Why the old loop did not sell (evidence: state/exits_log.jsonl):
  * login_blocked: pw/session.js allowed 2 logins/day and none after 10:59, and
    every exit run launched a fresh Chrome that needed a login (fixed in bug #2);
  * wrong prices: the fallback live_price() took TSETMC closingPriceDaily rows[-1]
    which is the OLDEST row (the API is newest-first) -> sells at 4,370 for a
    7,000+ stock (-36% "stop") that the broker silently rejected;
    lot_exits priced from a stale pnl.json snapshot (2,595 while the stock traded
    at 2,094) -> outside the daily band -> OrderId=0 -> "no_order_response";
  * pnl.build() crashed (a local variable shadowed broker_avg) so the corrected
    panel path was skipped and the broken fallback ran;
  * two decision paths (lot_exits.main + the pnl path) fired in the same run for
    the same symbol -> two stacked sell orders;
  * a stop-loss sell at the last price in a falling market sits above the bids and
    never fills.

Rules implemented here (strategy unchanged: TP 25 %, SL 3 %, lot-based):
  * every open journal lot is judged against its OWN fee-inclusive cost; shares
    not covered by journal lots are judged against the broker average;
  * lots bought today are not judged (order price != fill price);
  * live price = broker panel price (fresh snapshot), cross-checked with the TSETMC
    last trade; a missing/disagreeing price means "skip", never a guessed price;
  * sell limit: take-profit -> best bid (else last), stop-loss -> best bid (else the
    daily floor), always clamped into today's band;
  * quantity-based idempotency: qty to place = due qty - qty already in open sell
    orders - qty we submitted in the last few minutes (open-order list lag);
  * every decision and every order is logged (state/exits_log.jsonl,
    state/exit_orders.json).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / "src"))

STATE = BASE / "state"
JOURNAL = STATE / "trade_journal.jsonl"
EXITS_LOG = STATE / "exits_log.jsonl"
EXIT_ORDERS = STATE / "exit_orders.json"
PW = BASE / "pw"
NODE = os.environ.get("WARROBOT_NODE", r"C:\Program Files\nodejs\node.exe")

MANUAL_ONLY = {"افران", "خساپا"}
LOT_FEE_PCT = 0.89            # broker fee-inclusive basis (6,855 -> 6,916), as lot_exits
RECENT_SUBMIT_SEC = 600       # our own just-sent order may not be in the open list yet
MAX_PRICE_DISAGREE = 0.10     # panel vs TSETMC last trade


def config() -> dict:
    try:
        sell = json.loads((BASE / "config.json").read_text(encoding="utf-8")).get("sell") or {}
    except Exception:
        sell = {}
    out = {"tp": float(sell.get("target_pct") or 25.0),
           "sl": float(sell.get("stop_pct") or 3.0),
           "reprice_after_min": float(sell.get("reprice_after_min") or 10.0)}
    try:  # Hark V3: strategy v2/v3 exits (no TP, catastrophe stop 3 x limit = 9 %); v1 keeps sell.*
        if str(BASE) not in sys.path:
            sys.path.insert(0, str(BASE))
        import strategy_v2 as _s2
        p = _s2.live_params(BASE / "config.json")
        if p.get("version") == "v2":
            out["tp"] = float(p["tp_pct"]) if p.get("tp_pct") else 1e9
            out["sl"] = float(p["sl_pct"])
    except Exception:
        pass
    return out


# --------------------------------------------------------------------------- data
@dataclass
class Decision:
    symbol: str
    action: str                     # "sell" | "hold" | "skip"
    reason: str                     # take_profit | stop_loss | hold | <skip reason>
    held_qty: int = 0
    due_qty: int = 0
    qty: int = 0                    # what to place now
    live_price: float = 0.0
    pnl_pct: Optional[float] = None
    order_price: int = 0
    price_source: str = ""
    lots: list = field(default_factory=list)
    note: str = ""


def open_lots(rows: Optional[list] = None) -> dict:
    """symbol -> [{qty, buy, cost, at}] for every open journal lot."""
    if rows is None:
        rows = []
        if JOURNAL.exists():
            for line in JOURNAL.read_text(encoding="utf-8").splitlines():
                try:
                    rows.append(json.loads(line))
                except Exception:
                    continue
    out: dict = {}
    for d in rows:
        if d.get("status") != "open":
            continue
        sym = str(d.get("symbol") or "")
        qty, px = float(d.get("qty") or 0), float(d.get("price") or 0)
        if not sym or qty <= 0 or px <= 0:
            continue
        out.setdefault(sym, []).append({"qty": qty, "buy": px,
                                        "cost": px * (1 + LOT_FEE_PCT / 100.0),
                                        "at": str(d.get("at") or "")})
    return out


def _pct(price: float, cost: float) -> float:
    return (price / cost - 1.0) * 100.0 if cost > 0 else 0.0


def evaluate(position: dict, lots: list, live_price: float, tp: float, sl: float,
             today: str) -> Decision:
    """Pure decision for one held symbol (no I/O)."""
    sym = str(position["symbol"])
    held = int(float(position.get("qty") or 0))
    dec = Decision(symbol=sym, action="hold", reason="hold", held_qty=held, live_price=live_price)
    if sym in MANUAL_ONLY:
        dec.action, dec.reason = "skip", "manual_only"
        return dec
    if held <= 0:
        dec.action, dec.reason = "skip", "not_held"
        return dec
    if not live_price or live_price <= 0:
        dec.action, dec.reason = "skip", "no_live_price"
        return dec
    due_tp = due_sl = 0.0
    covered = 0.0
    for lot in lots or []:
        if lot["at"][:10] == today:
            covered += lot["qty"]          # fresh lot: neither judged nor re-judged by avg
            dec.lots.append({**lot, "pct": None, "verdict": "fresh_today_skip"})
            continue
        p = _pct(live_price, lot["cost"])
        verdict = "take_profit" if p >= tp else "stop_loss" if p <= -sl else "hold"
        dec.lots.append({**lot, "pct": round(p, 2), "verdict": verdict})
        covered += lot["qty"]
        if verdict == "take_profit":
            due_tp += lot["qty"]
        elif verdict == "stop_loss":
            due_sl += lot["qty"]
    # shares the journal does not know about: judge them on the broker average
    rest = max(0.0, held - covered)
    avg = float(position.get("avg_cost") or 0)
    if rest > 0 and avg > 0:
        p = _pct(live_price, avg)
        dec.pnl_pct = round(p, 2)
        if p >= tp:
            due_tp += rest
        elif p <= -sl:
            due_sl += rest
    elif avg > 0:
        dec.pnl_pct = round(_pct(live_price, avg), 2)
    due = min(held, int(due_tp + due_sl))
    if due <= 0:
        return dec
    dec.action = "sell"
    dec.reason = "stop_loss" if due_sl >= due_tp else "take_profit"
    dec.due_qty = due
    return dec


def sell_limit(reason: str, live_price: float, market: dict) -> tuple[int, str]:
    """Limit price for an exit: TP -> best bid else last; SL -> best bid else floor."""
    floor, ceiling = int(market.get("floor") or 0), int(market.get("ceiling") or 0)
    bids = market.get("bids") or []
    best_bid = int(bids[0][0]) if bids else 0
    if best_bid:
        px, src = best_bid, "best_bid"
    elif reason == "stop_loss" and floor:
        px, src = floor, "floor"
    else:
        px, src = int(round(live_price)), "last"
    if floor and px < floor:
        px, src = floor, src + "+clamp_floor"
    if ceiling and px > ceiling:
        px, src = ceiling, src + "+clamp_ceiling"
    return int(px), src


def price_ok(order_price: float, live_price: float, market: dict) -> bool:
    if order_price <= 0 or live_price <= 0:
        return False
    floor, ceiling = market.get("floor") or 0, market.get("ceiling") or 0
    if floor and ceiling:
        return floor <= order_price <= ceiling
    return abs(order_price / live_price - 1) <= 0.06      # no band known: stay near the tape


# ------------------------------------------------------------------- idempotency
def _load_orders() -> dict:
    try:
        return json.loads(EXIT_ORDERS.read_text(encoding="utf-8"))
    except Exception:
        return {}


def recent_submitted_qty(symbol: str, now: Optional[float] = None, book: Optional[dict] = None) -> int:
    now = now or time.time()
    book = book if book is not None else _load_orders()
    q = 0
    for rec in book.get("orders", []):
        if rec.get("symbol") == symbol and rec.get("submitted") and \
                now - float(rec.get("ts") or 0) < RECENT_SUBMIT_SEC and not rec.get("seen_open"):
            q += int(rec.get("qty") or 0)
    return q


def remember(rec: dict) -> None:
    book = _load_orders()
    orders = [o for o in book.get("orders", []) if time.time() - float(o.get("ts") or 0) < 3 * 86400]
    orders.append(rec)
    book["orders"] = orders
    try:
        EXIT_ORDERS.parent.mkdir(parents=True, exist_ok=True)
        EXIT_ORDERS.write_text(json.dumps(book, ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception:
        pass


def plan(positions: list[dict], lots_by_sym: dict, prices: dict, markets: dict,
         open_orders: Optional[list], cfg: dict, today: str,
         recent_qty: Optional[Callable[[str], int]] = None) -> list[Decision]:
    """Decide every held symbol. open_orders=None means "could not read" (fail safe)."""
    recent_qty = recent_qty or (lambda s: recent_submitted_qty(s))
    out = []
    for pos in positions:
        sym = str(pos["symbol"])
        live = float(prices.get(sym) or 0)
        dec = evaluate(pos, lots_by_sym.get(sym, []), live, cfg["tp"], cfg["sl"], today)
        if dec.action != "sell":
            out.append(dec)
            continue
        if open_orders is None:
            dec.action, dec.note = "skip", "open_orders_unknown"
            out.append(dec)
            continue
        open_sell = sum(int(float(o.get("qty") or 0)) - int(float(o.get("executed") or 0))
                        for o in open_orders
                        if str(o.get("symbol")) == sym and "فروش" in str(o.get("side") or ""))
        # an order we just sent may not be in the open list yet: count it once
        dec.qty = max(0, dec.due_qty - max(open_sell, recent_qty(sym)))
        if dec.qty <= 0:
            dec.action, dec.note = "skip", "sell_order_already_open"
            out.append(dec)
            continue
        m = markets.get(sym) or {}
        dec.order_price, dec.price_source = sell_limit(dec.reason, live, m)
        if not price_ok(dec.order_price, live, m):
            dec.action, dec.note = "skip", "price_out_of_band"
        out.append(dec)
    return out


# ---------------------------------------------------------------- live adapters
def fresh_panel_positions(max_age_min: float = 8.0, rebuild: bool = True) -> tuple[list, dict]:
    """(positions, {symbol: panel_price}) from pnl.json, rebuilt from the broker panel."""
    data = None
    if rebuild:
        try:
            import pnl
            d = pnl.build()
            data = d if isinstance(d, dict) else None
        except Exception as exc:
            print(f"[exit] pnl.build failed: {str(exc)[:120]}", flush=True)
    if data is None:
        try:
            data = json.loads((STATE / "pnl.json").read_text(encoding="utf-8"))
        except Exception:
            data = {}
    rows = data.get("positions") or []
    prices = {}
    try:
        at = datetime.strptime(str(data.get("at"))[:19], "%Y-%m-%d %H:%M:%S")
        age = (datetime.now() - at).total_seconds() / 60.0
    except Exception:
        age = 1e9
    if age <= max_age_min:
        prices = {str(r["symbol"]): float(r.get("price") or 0) for r in rows}
    return rows, prices


def cross_checked_prices(symbols: list[str], panel: dict) -> tuple[dict, dict, dict]:
    """(prices, markets, notes): panel price confirmed by TSETMC last trade."""
    prices, markets, notes = {}, {}, {}
    try:
        import price_discovery as pd_
    except Exception:
        pd_ = None
    for sym in symbols:
        m, last = {}, 0.0
        if pd_ is not None:
            try:
                m = pd_.market(sym)
            except Exception:
                m = {}
            try:
                last = float(pd_.last_trade(sym).get("last") or 0)
            except Exception:
                last = 0.0
        markets[sym] = m
        p = float(panel.get(sym) or 0)
        if p > 0 and last > 0 and abs(p / last - 1) > MAX_PRICE_DISAGREE:
            notes[sym] = f"price_disagree panel={p} tsetmc={last}"
            continue
        prices[sym] = p or last
    return prices, markets, notes


def read_open_orders() -> Optional[list]:
    try:
        p = subprocess.run([_node(), str(PW / "open_orders.js")], cwd=str(PW),
                           capture_output=True, text=True, timeout=300,
                           encoding="utf-8", errors="replace")
        for line in reversed((p.stdout or "").splitlines()):
            if line.strip().startswith("{"):
                d = json.loads(line)
                if d.get("error"):
                    return None
                return d.get("orders") or []
    except Exception:
        pass
    return None


def _node() -> str:
    return NODE if Path(NODE).exists() else "node"


def isin_of(symbol: str, position: Optional[dict] = None) -> str:
    if position and position.get("isin"):
        return str(position["isin"])
    try:
        import tsetmc as api
        return str((api.resolve(symbol) or {}).get("isin") or "")
    except Exception:
        return ""


def place_sell(symbol: str, isin: str, qty: int, price: int,
               at_epoch_ms: int = 0, priority: bool = False) -> dict:
    env = dict(os.environ)
    if at_epoch_ms:
        env["FLOW_AT_EPOCH_MS"] = str(int(at_epoch_ms))
    if priority:
        env["WARROBOT_UI_PRIORITY"] = "1"
    try:
        p = subprocess.run([_node(), str(PW / "flow.js"), symbol, isin, str(qty), str(price), "0", "sell"],
                           cwd=str(PW), capture_output=True, text=True, timeout=900,
                           encoding="utf-8", errors="replace", env=env)
        for line in reversed((p.stdout or "").splitlines()):
            if line.strip().startswith("{"):
                return json.loads(line)
        return {"message": "no_output", "stderr": (p.stderr or "")[-200:]}
    except Exception as exc:
        return {"message": f"error:{str(exc)[:80]}"}


def execute(decisions: list[Decision], positions: list[dict], tag: str, dry: bool = False,
            at_epoch_ms: int = 0, priority: bool = False,
            sender: Callable = place_sell) -> list[dict]:
    """Place every 'sell' decision; log every decision that is not a plain hold."""
    by_sym = {str(p["symbol"]): p for p in positions}
    results = []
    for d in decisions:
        if d.action == "hold":
            continue
        rec = {"at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "ts": time.time(),
               "symbol": d.symbol, "qty": d.qty, "price": d.order_price,
               "reason": f"{tag}_{d.reason}", "pnl_pct": d.pnl_pct, "live_price": d.live_price,
               "price_source": d.price_source, "due_qty": d.due_qty, "held_qty": d.held_qty,
               "action": d.action, "note": d.note, "submitted": False}
        if d.action == "sell" and not dry:
            isin = isin_of(d.symbol, by_sym.get(d.symbol))
            if not isin:
                rec.update(action="skip", note="no_isin")
            else:
                res = sender(d.symbol, isin, d.qty, d.order_price,
                             at_epoch_ms=at_epoch_ms, priority=priority)
                rec["message"] = str(res.get("message") or "")
                rec["submitted"] = bool(res.get("submitted"))
                rec["orderid"] = res.get("placed_orderid")
                if res.get("qty"):
                    rec["qty"] = int(res["qty"])        # flow.js caps by «آستانه حجم»
                if res.get("click_epoch_ms"):
                    rec["click_epoch_ms"] = res["click_epoch_ms"]
            remember(rec)
            if rec["submitted"]:
                _after_fill_hooks(rec, by_sym.get(d.symbol) or {})
        elif dry and d.action == "sell":
            rec["note"] = (rec["note"] + " dry").strip()
        results.append(rec)
        try:
            with EXITS_LOG.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except Exception:
            pass
    return results


def _after_fill_hooks(rec: dict, pos: dict) -> None:
    try:
        import trade_journal as tj
        tj.record_sell(rec["symbol"], rec["qty"], rec["price"], reason=rec["reason"], at=rec["at"])
        rec["journaled"] = True
    except Exception as exc:
        rec["journal_error"] = str(exc)[:80]
    try:
        avg = float(pos.get("avg_cost") or 0)
        profit = (float(rec["price"]) - avg) * int(rec["qty"]) if avg else 0.0
        if profit > 0:
            import profit_transfer
            rec["afran_transfer"] = profit_transfer.transfer(profit, source=rec["reason"])
    except Exception as exc:
        rec["afran_error"] = str(exc)[:80]


def stale_stop_orders(decisions: list[Decision], open_orders: list, markets: dict, cfg: dict,
                      book: Optional[dict] = None, now: Optional[float] = None) -> list[dict]:
    """OUR open stop-loss sells that sit above today's sell limit for too long.

    Operator orders (not in state/exit_orders.json) are never touched."""
    now = now or time.time()
    book = book if book is not None else _load_orders()
    ours = {str(o.get("orderid")): o for o in book.get("orders", []) if o.get("orderid")}
    out = []
    for d in decisions:
        if d.reason != "stop_loss" or d.note != "sell_order_already_open":
            continue
        limit, _ = sell_limit("stop_loss", d.live_price, markets.get(d.symbol) or {})
        for o in open_orders:
            oid = str(o.get("orderid"))
            if str(o.get("symbol")) != d.symbol or "فروش" not in str(o.get("side") or "") or oid not in ours:
                continue
            age_min = (now - float(ours[oid].get("ts") or now)) / 60.0
            if age_min >= cfg.get("reprice_after_min", 10.0) and float(o.get("price") or 0) > limit:
                out.append({"orderid": oid, "symbol": d.symbol, "price": float(o.get("price") or 0),
                            "new_limit": limit, "age_min": round(age_min, 1)})
    return out


def reprice_stale_stops(decisions, open_orders, markets, cfg) -> list[dict]:
    stale = stale_stop_orders(decisions, open_orders, markets, cfg)
    if not stale:
        return []
    try:
        import cancel_orders as co
        res = co.cancel([s["orderid"] for s in stale])
    except Exception as exc:
        res = {"ok": False, "error": str(exc)[:80]}
    recs = []
    for s in stale:
        rec = {"at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "action": "cancel_for_reprice",
               "symbol": s["symbol"], "orderid": s["orderid"], "old_price": s["price"],
               "new_limit": s["new_limit"], "result": str(res)[:200]}
        recs.append(rec)
        try:
            with EXITS_LOG.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except Exception:
            pass
    return recs


def prepare_cycle(rebuild_panel: bool = True) -> dict:
    """Read everything and decide; no order is sent here."""
    cfg = config()
    positions, panel = fresh_panel_positions(rebuild=rebuild_panel)
    positions = [p for p in positions if str(p.get("symbol")) not in MANUAL_ONLY]
    syms = [str(p["symbol"]) for p in positions]
    prices, markets, notes = cross_checked_prices(syms, panel)
    open_orders = read_open_orders()
    today = datetime.now().strftime("%Y-%m-%d")       # journal "at" uses the same clock
    decisions = plan(positions, open_lots(), prices, markets, open_orders, cfg, today)
    for d in decisions:
        if d.symbol in notes and d.action != "hold":
            d.action, d.note = "skip", (d.note + " " + notes[d.symbol]).strip()
        elif d.symbol in notes:
            d.action, d.reason, d.note = "skip", "price_disagree", notes[d.symbol]
    return {"cfg": cfg, "positions": positions, "markets": markets, "decisions": decisions,
            "open_orders": open_orders}


def run(tag: str, dry: bool = False, at_epoch_ms: int = 0, priority: bool = False,
        rebuild_panel: bool = True) -> dict:
    """Full live cycle: panel -> prices -> decisions -> orders."""
    c = prepare_cycle(rebuild_panel)
    decisions = c["decisions"]
    repriced = [] if dry else reprice_stale_stops(decisions, c["open_orders"] or [], c["markets"], c["cfg"])
    results = execute(decisions, c["positions"], tag, dry=dry, at_epoch_ms=at_epoch_ms, priority=priority)
    results.extend(repriced)
    return {"at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "tp": c["cfg"]["tp"], "sl": c["cfg"]["sl"],
            "positions": [asdict(d) for d in decisions], "exits": results,
            "open_orders_read": c["open_orders"] is not None}
