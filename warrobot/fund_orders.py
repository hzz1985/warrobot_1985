"""Execute the fundamentals strategy (F3_QVGM) against the broker — sells first, then buys.

    python fund_orders.py              run (DRY unless strategy.fund.live_orders is true)
    python fund_orders.py --dry        force dry: every flow.js call gets FLOW_DRY=1 (no click); the افران sale
                                       is simulated, so the buys show what the session WOULD do
    python fund_orders.py --plan-only  print the orders it would send right now (no browser at all)
    python fund_orders.py --force      also on a closed market day (rehearsals)

Scheduler (scheduler.py, Tehran time):  21:30 fund_live.py --plan (every day) ->
  08:41 morning_orders.py: order_candidates.prepare() returns the fund buys the cash allows (pre-open, 08:45:01)
  08:47 fund_orders.py (after "morning"): sell the افران excess, re-read the buying power, buy the F3 names
  09:05 / 09:40 / 10:20 / 11:20 / 12:10 fund_orders.py again: buys whatever is still missing as buying
        power arrives (افران is T+1: its sale money can buy T+2 stocks the same day)

Per run (all numbers recomputed from the CURRENT capital, see fund_ledger.capital):
  * target names = the newest rebalance plan (state/fund_plan.json) not yet executed, else the committed
    target in state/fund_state.json.  A new rebalance sells fund positions that left the target.
  * افران: sell floor((value - afran_share x total) / price) units when that excess is >= max(min_trade_rial,
    afran_min_excess_pct x total); never below the target; at most one افران sale per Tehran day.
  * buys in target order: a missing name gets stock_budget / n_hold; an existing fund name is topped up only
    when it is >= topup_drift x per-name below target; no order < min_trade_rial; a name with an open buy order
    or a buy submitted today (Tehran) is skipped; spend <= 99.5 % of the buying power.
  * prices: price_discovery (official band + best limits): buy crosses the best ask, else bids the ceiling
    (pre-open auction fills at the auction price); sells hit the best bid, افران never below 99.5 % of close.
War2 stop (war2_stop.py, strategy.fund.stop_fixed_pct > 0): stop sales go FIRST in every run — tonight's new
  stops from the plan plus every pending stop in state/fund_state.json "war2"; the whole held qty at
  exit_engine.sell_limit("stop_loss") (best bid, else the daily floor); retried each run until the broker no
  longer shows the name; our own open stop order above the current limit for >= 10 min is cancelled (re-placed
  next run).  A stopped name is never bought / topped up again in the same cycle, and its slot's money stays in
  cash (spendable cash is reduced by one per-name budget for each sold stop of the cycle).
Guards: MANUAL_ONLY (خساپا; افران except the excess sale) and legacy V3 names are never traded here.
Live buys go through order_candidates.execute (orders_log.jsonl, trade journal, idempotency).
Every decision is logged to state/fund_orders.jsonl; the last run to state/fund_run.json.
"""
from __future__ import annotations

import json
import math
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / "src"))
sys.path.insert(0, str(BASE.parent / "Tse2" / "orderflow"))
sys.path.insert(0, str(BASE.parent / "TeseRobot" / "src"))
import fund_ledger as FL          # noqa: E402
import strategy_fund as SF        # noqa: E402
import war2_stop as W2            # noqa: E402

STATE = BASE / "state"
PLAN = STATE / "fund_plan.json"
OLOG = STATE / "fund_orders.jsonl"
RUN = STATE / "fund_run.json"
ORDERS_LOG = STATE / "orders_log.jsonl"
PW = BASE / "pw"
NODE = os.environ.get("WARROBOT_NODE", r"C:\Program Files\nodejs\node.exe")
FEE_BUY, FEE_SELL_AFRAN = 0.003712, 0.0005
AFRAN_ISIN = "IRT1AFRN0001"
SPEND_SHARE = 0.995                 # broker blocks qty x price x (1 + fee); 0.5 % margin for rounding
PLAN_MAX_AGE_DAYS = 4


def tehran_now() -> datetime:
    """Tehran wall clock (naive). The OS clock is NTP-correct but its zone is US Pacific, and
    market_cal.now_tehran() silently falls back to the Pacific clock when distbot cannot be imported."""
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("Asia/Tehran")).replace(tzinfo=None)
    except Exception:
        from datetime import timezone
        return datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=3, minutes=30)


def _log(rec: dict) -> None:
    STATE.mkdir(parents=True, exist_ok=True)
    rec = {"at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
           "at_tehran": tehran_now().strftime("%Y-%m-%d %H:%M:%S"), **rec}
    with OLOG.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
    print(json.dumps(rec, ensure_ascii=False, default=str)[:400], flush=True)


# ------------------------------------------------------------------ targets
def current_target() -> tuple[list, dict, bool]:
    """(target names in buy order, plan, is_new_rebalance)."""
    st = FL.load_state()
    try:
        plan = json.loads(PLAN.read_text(encoding="utf-8"))
    except Exception:
        plan = {}
    fresh = False
    try:
        fresh = datetime.now() - datetime.strptime(plan["at"], "%Y-%m-%d %H:%M:%S") <= timedelta(days=PLAN_MAX_AGE_DAYS)
    except Exception:
        pass
    if fresh and plan.get("action") == "rebalance" and plan.get("target") and \
            str(plan.get("session")) != str(st.get("last_rebalance_session")):
        return [FL.norm(s) for s in plan["target"]], plan, True
    return [FL.norm(s) for s in (st.get("target") or [])], plan, False


def commit_rebalance(plan: dict) -> None:
    st = FL.load_state()
    st.update(last_rebalance_session=plan.get("session"), last_rebalance_at=plan.get("at"),
              target=[FL.norm(s) for s in (plan.get("target") or [])], target_committed_tehran=tehran_now().strftime("%Y-%m-%d %H:%M"))
    if "war2" in st:
        W2.roll_cycle(st, plan.get("session"))
    FL.save_state(st)


def plan_is_fresh(plan: dict) -> bool:
    try:
        return datetime.now() - datetime.strptime(plan["at"], "%Y-%m-%d %H:%M:%S") <= timedelta(days=PLAN_MAX_AGE_DAYS)
    except Exception:
        return False


def recent_stop_submits(minutes: float = 15.0) -> set:
    """symbols with a live War2 stop sell we submitted in the last few minutes (server clock)."""
    out, now = set(), datetime.now()
    for r in _jsonl(OLOG, last=500):
        if r.get("event") == "order" and r.get("reason") == W2.REASON and r.get("submitted") and not r.get("dry"):
            try:
                if (now - datetime.strptime(r["at"], "%Y-%m-%d %H:%M:%S")).total_seconds() <= minutes * 60:
                    out.add(FL.norm(r.get("symbol")))
            except Exception:
                pass
    return out


def war2_stops(plan: dict, st: dict) -> dict:
    """{symbol: stop record} to act on now: pending stops from the state + new stops of a fresh plan."""
    out = {s: dict(v, symbol=s, status="pending") for s, v in W2.pending(st).items()}
    if plan_is_fresh(plan):
        for x in plan.get("stops") or []:
            s = FL.norm(x.get("symbol"))
            if not s or s in out:
                continue
            cur = (W2.w2_state(st)["stops"]).get(s) or {}
            if cur.get("status") == "sold" and str(cur.get("trigger_session")) == str(x.get("trigger_session")):
                continue
            out[s] = dict(x, symbol=s)
    return out


# ------------------------------------------------------------------ today's activity (Tehran day)
def tehran_day() -> str:
    return tehran_now().strftime("%Y-%m-%d")


def _jsonl(p: Path, last: int = 3000) -> list[dict]:
    out = []
    try:
        for line in p.read_text(encoding="utf-8").splitlines()[-last:]:
            try:
                out.append(json.loads(line))
            except Exception:
                pass
    except Exception:
        pass
    return out


def submitted_today(side: str = "buy") -> set:
    """symbols with a live order of this side accepted today (Tehran day; the server clock is Pacific)."""
    day = tehran_day()
    out = set()
    for r in _jsonl(ORDERS_LOG):
        if str(r.get("at_tehran") or "")[:10] == day and r.get("submitted") and r.get("symbol") \
                and str(r.get("side") or "buy") == side:
            out.add(FL.norm(r["symbol"]))
    for r in _jsonl(OLOG):
        if str(r.get("at_tehran") or "")[:10] == day and r.get("event") == "order" and r.get("side") == side \
                and r.get("submitted") and not r.get("dry"):
            out.add(FL.norm(r["symbol"]))
    return out


def open_order_book(open_orders: list) -> tuple[set, set, float]:
    """(symbols with an open buy, symbols with an open sell, cash reserved by open buys)."""
    buys, sells, reserved = set(), set(), 0.0
    for o in open_orders or []:
        side = str(o.get("side") or "")
        sym = FL.norm(o.get("symbol"))
        if "فروش" in side or side.lower() == "sell":
            sells.add(sym)
            continue
        buys.add(sym)
        try:
            left = float(o.get("qty") or 0) - float(o.get("executed") or 0)
            reserved += max(0.0, left) * float(o.get("price") or 0) * (1 + FEE_BUY)
        except Exception:
            pass
    return buys, sells, reserved


# ------------------------------------------------------------------ prices
def _buy_px(sym: str, ref: float) -> tuple[int, str]:
    try:
        import price_discovery as pdsc
        d = pdsc.buy_price(sym, ref)
        if d.get("price"):
            return int(d["price"]), str(d.get("source"))
    except Exception:
        pass
    return (int(round(ref * 1.02)), "close+2%") if ref else (0, "none")


def _sell_px(sym: str, ref: float, floor_share: float = 0.0) -> tuple[int, str]:
    px, src = 0, "none"
    try:
        import price_discovery as pdsc
        d = pdsc.sell_price(sym, ref)
        px, src = int(d.get("price") or 0), str(d.get("source"))
    except Exception:
        pass
    if not px and ref:
        px, src = int(round(ref * 0.98)), "close-2%"
    if floor_share and ref and px < ref * floor_share:
        px, src = int(math.ceil(ref * floor_share)), src + "+min_" + str(floor_share)
    return px, src


def _stop_px(sym: str, ref: float) -> tuple[int, str]:
    """War2 stop limit: exit_engine.sell_limit("stop_loss") = best bid, else today's floor (clamped to the band)."""
    market = {}
    try:
        import price_discovery as pdsc
        market = pdsc.market(sym, ref) or {}
    except Exception:
        pass
    try:
        import exit_engine as ee
        px, src = ee.sell_limit("stop_loss", ref, market)
    except Exception:
        px, src = 0, "none"
    if not px and ref:                       # no book and no band: 3 % under the reference (the TSE band)
        px, src = int(round(ref * 0.97)), "ref-3%"
    return int(px), "war2:" + str(src)


def _ref_close(sym: str, plan: dict) -> float:
    for r in (plan.get("ranked_top") or []):
        if FL.norm(r.get("sym")) == sym and r.get("close"):
            return float(r["close"])
    for b in (plan.get("buys") or []):
        if FL.norm(b.get("symbol")) == sym and b.get("ref_price"):
            return float(b["ref_price"])
    try:
        import price_discovery as pdsc
        lt = pdsc.last_trade(sym)
        return float(lt.get("yesterday") or lt.get("close") or 0)
    except Exception:
        return 0.0


# ------------------------------------------------------------------ the decision (no orders)
def plan_now(cash: float, open_orders: list | None = None, sold_afran_today: bool | None = None,
             bought_today: set | None = None, buy_px=None, sell_px=None, ref_close=None,
             simulate_afran_sale: bool = False, stop_px=None) -> dict:
    fc = FL.fund_config()
    _, cfg = SF.load_config(BASE / "config.json")
    w2cfg = W2.config(BASE / "config.json")
    buy_px = buy_px or _buy_px
    sell_px = sell_px or _sell_px
    stop_px = stop_px or _stop_px
    open_buys, open_sells, reserved = open_order_book(open_orders or [])
    bought_today = submitted_today("buy") if bought_today is None else bought_today
    if sold_afran_today is None:
        sold_afran_today = FL.AFRAN in submitted_today("sell")
    cap = FL.capital(cash, pending_buys_rial=reserved)
    target, plan, new_reb = current_target()
    ref_close = ref_close or (lambda s: _ref_close(s, plan))
    out = {"capital": cap, "target": target, "new_rebalance": new_reb, "plan_session": plan.get("session"),
           "plan_at": plan.get("at"), "afran_sell": None, "stop_sells": [], "sells": [], "buys": [], "notes": [],
           "war2": {"name": w2cfg["name"], "stop_fixed_pct": w2cfg["stop_fixed_pct"], "active": w2cfg["active"]}}
    total = cap["total"]
    per_name = cap["stock_budget"] / max(1, cfg.n_hold)
    out["per_name"] = round(per_name, 0)
    min_trade = fc["min_trade_rial"]
    leg = set(cap["legacy_v3"])
    held = dict(cap["fund"])
    if not target:
        out["notes"].append("no target yet (no rebalance plan): nothing to buy")

    # 0) War2 stop sales: first, every run, until the broker no longer shows the name
    st = FL.load_state()
    blocked, stop_names, reserve = set(), set(), 0.0
    if w2cfg["active"]:
        stops = war2_stops(plan, st)
        _, snap = FL.positions_snapshot()
        out["stops"] = []
        for s, x in stops.items():
            if s in FL.MANUAL_ONLY or s in W2.MANUAL_ONLY or s in leg:
                out["notes"].append(f"{s}: War2 never judges manual-only / legacy V3"); continue
            stop_names.add(s)
            qty = int((snap.get(s) or {}).get("qty") or 0)
            rec = {"symbol": s, "status": x.get("status"), "trigger_session": x.get("trigger_session"),
                   "stop_price": x.get("stop_price"), "close": x.get("close"), "held_qty": qty}
            out["stops"].append(rec)
            if qty <= 0:
                rec["status"] = "sold"; continue
            if s in open_sells:
                rec["order"] = "sell_open"
                out["notes"].append(f"{s}: War2 stop sell already open"); continue
            if s in recent_stop_submits():
                rec["order"] = "submitted_recently"       # the open-order list can lag our own order
                out["notes"].append(f"{s}: War2 stop sell submitted < 15 min ago"); continue
            ref = float(x.get("close") or 0) or ref_close(s)
            px, src = stop_px(s, ref)
            if px <= 0:
                out["notes"].append(f"{s}: War2 stop, no price"); continue
            out["stop_sells"].append({"side": "sell", "symbol": s, "qty": qty, "price": px, "price_source": src,
                                      "reason": W2.REASON, "value": round(qty * px, 0),
                                      "stop_price": x.get("stop_price"), "trigger_session": x.get("trigger_session")})
        blocked = W2.blocked(st, [{"symbol": s} for s in stop_names], new_reb)
        # cash waits: each stop of this cycle that is already sold keeps one per-name budget out of the buys
        w2st = W2.w2_state(st)["stops"]
        for s in (W2.stopped_in_cycle(st, st.get("last_rebalance_session")) if not new_reb else set()):
            v = w2st.get(s) or {}
            if s in held or v.get("status") != "sold":
                continue
            freed = float(v.get("qty") or 0) * float(v.get("close") or 0)
            reserve += min(per_name, freed) if freed > 0 else per_name
        out["stopped_this_cycle"] = sorted(blocked)
        if reserve:
            out["cash_reserved_for_stopped"] = round(reserve, 0)

    # 1) rebalance sells: fund positions that left the target (only when a NEW rebalance is executed)
    if new_reb:
        for s, v in held.items():
            if s in target or s in FL.MANUAL_ONLY or s in leg or s in stop_names:
                continue
            if s in open_sells:
                out["notes"].append(f"{s}: sell already open"); continue
            qty = FL.fund_qty(s)
            ref = ref_close(s)
            px, src = sell_px(s, ref)
            if qty > 0 and px > 0:
                out["sells"].append({"side": "sell", "symbol": s, "qty": qty, "price": px, "price_source": src,
                                     "reason": "fund_rebalance", "value": round(qty * px, 0)})

    # 2) افران excess above its target (manage_afran only)
    afran_proceeds = 0.0
    if fc["manage_afran"] and cap["afran_qty"] > 0:
        excess = cap["afran_value"] - cap["afran_target"]
        threshold = max(min_trade, fc["afran_min_excess_pct"] * total)
        out["afran_excess"] = round(excess, 0)
        if excess < threshold:
            out["notes"].append(f"افران excess {excess:,.0f} < threshold {threshold:,.0f}: no sale")
        elif sold_afran_today:
            out["notes"].append("افران already sold today")
        elif FL.AFRAN in open_sells:
            out["notes"].append("افران sell order already open")
        else:
            ref = cap["afran_price"] or ref_close(FL.AFRAN)
            qty = int(excess // ref) if ref > 0 else 0
            qty = min(qty, int(cap["afran_qty"]))
            px, src = sell_px(FL.AFRAN, ref, floor_share=0.995)
            if qty > 0 and px > 0:
                out["afran_sell"] = {"side": "sell", "symbol": FL.AFRAN, "qty": qty, "price": px,
                                     "price_source": src, "reason": "afran_excess_over_target",
                                     "value": round(qty * ref, 0),
                                     "afran_after": round(cap["afran_value"] - qty * ref, 0)}
                afran_proceeds = qty * min(px, ref) * (1 - FEE_SELL_AFRAN)

    # 3) buys in target order
    spend = max(0.0, max(0.0, cash) * SPEND_SHARE - reserve)
    if simulate_afran_sale and afran_proceeds:
        spend += afran_proceeds * SPEND_SHARE
        out["notes"].append(f"dry: buying power includes the simulated افران sale ({afran_proceeds:,.0f})")
    out["spendable"] = round(spend, 0)
    waiting = []
    for rank, s in enumerate(target, 1):
        if s in FL.MANUAL_ONLY or s in leg:
            out["notes"].append(f"{s}: manual-only / legacy V3, not a fund slot"); continue
        if s in blocked:
            out["notes"].append(f"{s}: War2 stopped this cycle, not bought again until the next rebalance"); continue
        if s in open_buys or s in bought_today:
            out["notes"].append(f"{s}: buy already open / submitted today"); continue
        cur = float(held.get(s) or 0)
        if cur <= 0:
            need = per_name
        elif per_name - cur >= fc["topup_drift"] * per_name:
            need = per_name - cur
        else:
            continue
        if need < min_trade:
            out["notes"].append(f"{s}: need {need:,.0f} < min trade"); continue
        if spend < min_trade:
            waiting.append(s); continue
        ref = ref_close(s)
        px, src = buy_px(s, ref)
        if px <= 0:
            out["notes"].append(f"{s}: no price"); continue
        budget = min(need, spend)
        qty = int(budget / (px * (1 + FEE_BUY)))
        if qty <= 0 or qty * px < min_trade * 0.9:
            waiting.append(s); continue
        cost = qty * px * (1 + FEE_BUY)
        spend -= cost
        out["buys"].append({"side": "buy", "symbol": s, "qty": qty, "price": px, "price_source": src,
                            "budget": round(budget, 0), "target_rial": round(per_name, 0),
                            "held_rial": round(cur, 0), "ref_close": ref, "order_value": round(cost, 0),
                            "reason": f"F3_QVGM target #{rank}" + (" top-up" if cur > 0 else "")})
    if waiting:
        out["waiting_for_cash"] = waiting
    return out


# ------------------------------------------------------------------ order_candidates hook (pre-open buys)
def candidates_plan(out: dict, free_balance, open_orders_fn, isin_fn) -> dict:
    """order_candidates.prepare() in replace mode: the fund's buys for the cash available NOW
    (same record format as the V3 planner, so morning_orders / order_supervisor send them unchanged)."""
    if not FL.fund_config()["live_orders"]:
        out["strategy"] = "fund"
        out["note"] = "fund: live_orders=false, rehearsal only (fund_orders.py --dry)"
        return out
    free = float(free_balance()) or FL.fresh_cash_snapshot()
    oo = open_orders_fn() or []
    p = plan_now(free, oo)
    out["free_balance"] = round(free, 0)
    out["strategy"] = "fund"
    out["fund"] = {k: p.get(k) for k in ("capital", "per_name", "target", "notes", "waiting_for_cash", "afran_sell",
                                         "new_rebalance", "plan_session")}
    out["total_assets"] = p["capital"]["total"]
    out["target_per_symbol"] = p["per_name"]
    out["planned_symbols"] = [b["symbol"] for b in p["buys"]]
    for b in p["buys"]:
        isin = isin_fn(b["symbol"])
        if not isin:
            out["orders"].append({"symbol": b["symbol"], "ok": False, "reason": "isin_not_found"}); continue
        out["planned"].append({"at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "symbol": b["symbol"],
                               "isin": isin, "budget": b["budget"], "price": b["price"], "qty": b["qty"],
                               "price_source": b["price_source"], "reason_text": b["reason"], "strategy": "fund"})
    if not out["planned"]:
        out["note"] = "fund: nothing to buy now (" + "; ".join(p["notes"][:3] or ["no cash / all placed"]) + ")"
    return out


# ------------------------------------------------------------------ sending
def _isin(symbol: str) -> str:
    if symbol == FL.AFRAN:
        return AFRAN_ISIN
    try:
        import tsetmc as api
        return str((api.resolve(symbol) or {}).get("isin") or "")
    except Exception:
        return ""


def _flow(symbol, isin, qty, price, side, dry) -> dict:
    env = dict(os.environ)
    if dry:
        env["FLOW_DRY"] = "1"
    args = [NODE if Path(NODE).exists() else "node", str(PW / "flow.js"), symbol, isin, str(int(qty)), str(int(price))]
    if side == "sell":
        args += ["0", "sell"]
    try:
        p = subprocess.run(args, cwd=str(PW), capture_output=True, text=True, timeout=900,
                           encoding="utf-8", errors="replace", env=env)
        for line in reversed((p.stdout or "").splitlines()):
            if line.strip().startswith("{"):
                return json.loads(line)
        return {"message": "no_output", "stderr": (p.stderr or "")[-200:]}
    except Exception as exc:
        return {"message": f"error:{str(exc)[:80]}"}


def _send_sell(o: dict, dry: bool) -> dict:
    isin = _isin(o["symbol"])
    if not isin:
        rec = {"event": "order", "dry": dry, **o, "message": "isin_not_found", "submitted": False}
        _log(rec); return rec
    data = _flow(o["symbol"], isin, o["qty"], o["price"], "sell", dry)
    sub = bool(data.get("submitted")) and not dry
    rec = {"event": "order", "dry": dry, **o, "isin": isin, "message": data.get("message"), "submitted": sub,
           "orderid": data.get("placed_orderid"), "flow_qty": data.get("qty")}
    _log(rec)
    if sub:
        with ORDERS_LOG.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                                 "at_tehran": tehran_now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
                                 "symbol": o["symbol"], "side": "sell", "qty": data.get("qty") or o["qty"],
                                 "price": data.get("placed_price") or o["price"], "reason": o.get("reason"),
                                 "strategy": "fund", "message": data.get("message"),
                                 "orderid": data.get("placed_orderid"), "submitted": True}, ensure_ascii=False) + "\n")
        if o["symbol"] != FL.AFRAN:
            try:
                import trade_journal as tj
                tj.record_sell(o["symbol"], data.get("qty") or o["qty"], o["price"], reason=o.get("reason"),
                               at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
            except Exception:
                pass
    return rec


def _send_buy(b: dict, dry: bool) -> dict:
    isin = _isin(b["symbol"])
    if not isin:
        rec = {"event": "order", "dry": dry, **b, "message": "isin_not_found", "submitted": False}
        _log(rec); return rec
    if dry:
        data = _flow(b["symbol"], isin, b["qty"], b["price"], "buy", True)
        rec = {"event": "order", "dry": True, **b, "isin": isin, "message": data.get("message"), "submitted": False,
               "flow_qty": data.get("qty"), "flow_bound": data.get("bound")}
        _log(rec); return rec
    import order_candidates as oc
    res = oc.execute({"planned": [{"at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "symbol": b["symbol"],
                                   "isin": isin, "budget": b["budget"], "price": b["price"], "qty": b["qty"],
                                   "price_source": b["price_source"], "reason_text": b["reason"],
                                   "strategy": "fund"}]}, dry=False)
    o = (res.get("orders") or [{}])[-1]
    rec = {"event": "order", "dry": False, **b, "isin": isin, "message": o.get("message") or o.get("skipped"),
           "submitted": bool(o.get("submitted")), "orderid": o.get("orderid")}
    _log(rec)
    return rec


# ------------------------------------------------------------------ War2 bookkeeping (live runs only)
def war2_persist(p: dict, results: list) -> None:
    """record tonight's new stops as pending, count order attempts, close the ones the broker no longer shows."""
    try:
        st = FL.load_state()
        _, plan, _ = current_target()
        now = tehran_now().strftime("%Y-%m-%d %H:%M")
        if plan_is_fresh(plan):
            W2.merge_plan_stops(st, [x for x in plan.get("stops") or [] if x.get("status") == "new"], now)
        w = W2.w2_state(st)
        for r in results:
            if r.get("reason") == W2.REASON and FL.norm(r.get("symbol")) in w["stops"]:
                v = w["stops"][FL.norm(r["symbol"])]
                v["orders"] = int(v.get("orders") or 0) + 1
                v["last_order_tehran"] = now
                v["last_order_msg"] = str(r.get("message"))[:80]
                v["last_order_submitted"] = bool(r.get("submitted"))
        _, snap = FL.positions_snapshot()
        if snap:                         # only with a broker snapshot: no snapshot never closes a stop
            W2.mark_sold(st, set(snap), now)
        FL.save_state(st)
    except Exception as exc:
        _log({"event": "war2_persist_error", "error": str(exc)[:200]})


def war2_reprice(open_orders: list, now: float | None = None) -> list:
    """cancel OUR open War2 stop sells that sit above today's stop limit for >= 10 min (re-placed next run)."""
    now = now or time.time()
    ours = {}
    for r in _jsonl(OLOG):
        if r.get("event") == "order" and r.get("reason") == W2.REASON and r.get("submitted") and r.get("orderid"):
            try:
                ts = datetime.strptime(r["at"], "%Y-%m-%d %H:%M:%S").timestamp()
            except Exception:
                ts = now
            ours[str(r["orderid"])] = ts
    stale = []
    for o in open_orders or []:
        oid = str(o.get("orderid") or o.get("order_id") or "")
        side = str(o.get("side") or "")
        if oid not in ours or not ("فروش" in side or side.lower() == "sell"):
            continue
        sym = FL.norm(o.get("symbol"))
        limit, _ = _stop_px(sym, float(o.get("price") or 0))
        if (now - ours[oid]) / 60.0 >= 10 and limit and float(o.get("price") or 0) > limit:
            stale.append({"orderid": oid, "symbol": sym, "price": o.get("price"), "new_limit": limit})
    if not stale:
        return []
    try:
        import cancel_orders as co
        res = co.cancel([x["orderid"] for x in stale])
    except Exception as exc:
        res = {"ok": False, "error": str(exc)[:80]}
    for x in stale:
        _log({"event": "war2_cancel_for_reprice", **x, "result": str(res)[:200]})
    return stale


# ------------------------------------------------------------------ run
def run(dry: bool = False, force: bool = False, plan_only: bool = False) -> dict:
    fc = FL.fund_config()
    if not fc["enabled"]:
        _log({"event": "skipped", "reason": "strategy.fund.enabled is false"})
        return {"skipped": True}
    dry = dry or not fc["live_orders"]
    if not force:
        try:
            import market_cal
            if not market_cal.market_day(tehran_now()):
                _log({"event": "skipped", "reason": "market closed"})
                return {"skipped": "market_closed"}
        except Exception:
            pass
    import order_candidates as oc
    report = {"started_tehran": tehran_now().strftime("%Y-%m-%d %H:%M:%S"), "dry": dry}
    if plan_only:
        p = plan_now(FL.cash_snapshot(), [], simulate_afran_sale=True)
        report["plan"] = p
        print(json.dumps(report, ensure_ascii=False, indent=1, default=str))
        return report
    try:
        import pw_lock as _pl
        guard = _pl.guard("fund_orders", wait=240)
    except Exception:
        guard = None
    import contextlib
    with (guard or contextlib.nullcontext(True)) as got:
        if not got:
            _log({"event": "playwright_lock_timeout"}); return {"skipped": "lock"}
        cash = oc._free_balance() or FL.fresh_cash_snapshot()
        oo = oc._open_orders()
        p = plan_now(cash, oo, simulate_afran_sale=dry)
        report["plan"] = p
        _log({"event": "plan", "dry": dry, "cash": cash, "total": p["capital"]["total"], "per_name": p["per_name"],
              "afran_sell": p["afran_sell"], "sells": [s["symbol"] for s in p["sells"]],
              "buys": [(b["symbol"], b["qty"], b["price"]) for b in p["buys"]], "notes": p["notes"][:8]})
        results = []
        if p.get("war2", {}).get("active"):
            report["war2_reprice"] = [] if dry else war2_reprice(oo)
        for o in p.get("stop_sells", []) + ([p["afran_sell"]] if p["afran_sell"] else []) + p["sells"]:
            results.append(_send_sell(o, dry))
        if not dry and p.get("war2", {}).get("active"):
            war2_persist(p, results)
        if not dry and any(r.get("submitted") for r in results):
            # the sale money becomes buying power when it fills: give it a moment, then re-plan the buys
            time.sleep(20)
            cash = oc._free_balance() or FL.fresh_cash_snapshot()
            oo = oc._open_orders()
            p2 = plan_now(cash, oo)
            report["replan"] = {"cash": cash, "buys": [b["symbol"] for b in p2["buys"]],
                                "waiting_for_cash": p2.get("waiting_for_cash")}
            buys = p2["buys"]
        else:
            buys = p["buys"]
        for b in buys:
            results.append(_send_buy(b, dry))
        if not dry and p["new_rebalance"]:
            _, plan, _ = current_target()
            commit_rebalance(plan)
            report["committed_rebalance_session"] = plan.get("session")
        report["results"] = results
    report["finished_tehran"] = tehran_now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        RUN.write_text(json.dumps(report, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    except Exception:
        pass
    return report


if __name__ == "__main__":
    if "--plan-file" in sys.argv:          # rehearsal against a plan written by fund_live.py --plan --out X
        PLAN = Path(sys.argv[sys.argv.index("--plan-file") + 1])
    r = run(dry="--dry" in sys.argv, force="--force" in sys.argv, plan_only="--plan-only" in sys.argv)
    if "--plan-only" not in sys.argv:
        print(json.dumps({k: r.get(k) for k in ("dry", "skipped", "committed_rebalance_session")} |
                         {"orders": [(x.get("side"), x.get("symbol"), x.get("qty"), x.get("price"), x.get("message"),
                                      x.get("submitted")) for x in r.get("results", [])]},
                         ensure_ascii=False, default=str)[:3000])
