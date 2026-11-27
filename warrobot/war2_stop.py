"""War2 = F3_QVGM + a fixed stop-loss (owner approved 2026-11-27; research: reports/hark_backtest_fund_sl/SUMMARY.md).

Rule
  * Every F3 (fund) position is judged once a day, on the DAILY CLOSE, in the 21:30 nightly plan
    (fund_live.py --plan).  Trigger: close <= entry cost x (1 - stop_fixed_pct/100) x adj.
      entry cost = broker average buy price x (1 + buy fee 0.3712 %)    (fee-inclusive cost per share)
      adj        = product of TSETMC price adjustments (priceYesterday / previous close) since the cost was
                   recorded, so a capital raise / dividend ex-date never fires a stop (same as the backtest's
                   total-return basis).  The basis is re-recorded whenever the broker qty or average changes.
  * Next morning fund_orders.py sells the WHOLE position FIRST (before any other order), limit =
    exit_engine.sell_limit("stop_loss", ...) = best bid, else the daily floor.  A stop that does not fill
    (floor queue / صف فروش) is retried on every run until the broker no longer shows the position; our own
    open stop order that sits above the current limit for >= 10 min is cancelled and re-placed next run.
  * A stopped name is NOT bought again in the same rebalance cycle (21 sessions); its money stays in cash
    (no replacement name).  At the next rebalance it may come back only through the normal top-10 selection.
  * No take-profit.  افران / خساپا (MANUAL_ONLY) and legacy V3 positions (آباد: V3 exits in exit_engine)
    are never judged.  sell_monitor / exit_engine never act on F3 positions (fund_ledger ownership).

Config (config.json strategy.fund):  stop_fixed_pct  0 = off (pure F3), War2 = 15;  name  display name ("War2").
strategy.version stays "fund" (scheduler keys on it).

State (state/fund_state.json "war2"):
  basis {sym: {cost_ps, avg_cost, qty, since (session yyyymmdd), cost_src}}       written by the nightly plan
  stops {sym: {trigger_session, trigger_close, cost_ps, stop_price, cycle, status pending|sold, ...}}
                                                                                   written by live fund_orders runs
  history [ closed stops, newest last, max 100 ]
"""
from __future__ import annotations

import json
import math
from pathlib import Path

BASE = Path(__file__).resolve().parent
REASON = "war2_stop"
FEE_BUY = 0.003712
MANUAL_ONLY = {"افران", "خساپا"}
ADJ_TOL = 0.001          # priceYesterday differs from the previous close by more than this -> adjustment
HISTORY_MAX = 100


def norm(sym) -> str:
    return str(sym or "").replace("ي", "ی").replace("ك", "ک").strip()


def config(path=None) -> dict:
    """{'name', 'stop_fixed_pct', 'active'} from config.json strategy.fund (missing / bad -> off)."""
    try:
        raw = (json.loads(Path(path or BASE / "config.json").read_text(encoding="utf-8")).get("strategy") or {}) \
            .get("fund") or {}
    except Exception:
        raw = {}
    try:
        pct = float(raw.get("stop_fixed_pct") or 0)
    except Exception:
        pct = 0.0
    if not (0.0 <= pct <= 50.0) or math.isnan(pct):
        pct = 0.0
    name = str(raw.get("name") or raw.get("variant") or "F3_QVGM")
    return {"name": name, "stop_fixed_pct": pct, "active": pct > 0}


# ------------------------------------------------------------------ pure math
def entry_cost(pos: dict, fee_buy: float = FEE_BUY) -> tuple[float, str]:
    """fee-inclusive cost per share from a broker position row (avg_cost = raw average buy price)."""
    try:
        avg = float(pos.get("avg_cost") or 0)
    except Exception:
        avg = 0.0
    if avg > 0:
        return avg * (1.0 + fee_buy), "broker_avg+fee"
    try:
        avf = float(pos.get("avg_cost_fee") or 0)
    except Exception:
        avf = 0.0
    if avf > 0:                     # broker break-even average (already fee-inclusive, a bit higher)
        return avf, "broker_avg_cost_fee"
    return 0.0, "none"


def adj_factor(rows: list, since: int | None) -> float:
    """rows = [[dEven, priceYesterday, close, ntr, value], ...] oldest first.  Product of
    priceYesterday / previous close over sessions strictly after `since` (capital raise / dividend gaps)."""
    if not since:
        return 1.0
    adj, prev_c = 1.0, None
    for r in rows or []:
        d, py, c = int(r[0]), float(r[1] or 0), float(r[2] or 0)
        if d > int(since) and prev_c and py > 0 and abs(py / prev_c - 1.0) > ADJ_TOL:
            adj *= py / prev_c
        if c > 0:
            prev_c = c
    return adj


def stop_price(cost_ps: float, pct: float, adj: float = 1.0) -> float:
    return cost_ps * (1.0 - pct / 100.0) * adj if cost_ps > 0 and pct > 0 else 0.0


def dist_pct(close: float, stop: float) -> float | None:
    """how far the close sits above the stop, % of the close (negative = through the stop)."""
    if not close or not stop:
        return None
    return round((close - stop) / close * 100.0, 2)


def update_basis(basis: dict, positions: dict, session: int) -> dict:
    """new basis map: re-record a name when its broker qty or average changed (buy, top-up, partial sale,
    bonus shares credited) -> `since` = this session (adjustments before it are already in the broker average)."""
    out = {}
    for s, p in positions.items():
        s = norm(s)
        cost, src = entry_cost(p)
        old = basis.get(s) or {}
        same = old and abs(float(old.get("qty") or 0) - float(p.get("qty") or 0)) < 0.5 and \
            abs(float(old.get("avg_cost") or 0) - float(p.get("avg_cost") or p.get("avg_cost_fee") or 0)) < 0.5
        if same:
            out[s] = dict(old)
        else:
            out[s] = {"cost_ps": round(cost, 2), "avg_cost": float(p.get("avg_cost") or p.get("avg_cost_fee") or 0),
                      "qty": float(p.get("qty") or 0), "since": int(session), "cost_src": src}
    return out


def judge(sym: str, pos: dict, rows: list, session: int, basis_rec: dict, pct: float) -> dict:
    """one held F3 name -> risk row {symbol, cost_ps, adj, stop_price, close, dist_pct, status}.
    status: ok | triggered | no_cost | no_close_today."""
    cost = float((basis_rec or {}).get("cost_ps") or 0) or entry_cost(pos)[0]
    adj = adj_factor(rows, (basis_rec or {}).get("since"))
    by = {int(r[0]): r for r in rows or [] if float(r[2] or 0) > 0 and float(r[3] or 0) > 0}
    close = float(by[session][2]) if session in by else 0.0
    sp = stop_price(cost, pct, adj)
    row = {"symbol": norm(sym), "qty": float(pos.get("qty") or 0), "avg_cost": float(pos.get("avg_cost") or 0),
           "cost_ps": round(cost, 2), "cost_src": (basis_rec or {}).get("cost_src") or entry_cost(pos)[1],
           "adj": round(adj, 6), "stop_price": round(sp, 2), "close": close, "dist_pct": dist_pct(close, sp),
           "pnl_pct": round((close / cost - 1) * 100, 2) if cost and close else None, "status": "ok"}
    if cost <= 0:
        row["status"] = "no_cost"
    elif not close:
        row["status"] = "no_close_today"
    elif close <= sp:
        row["status"] = "triggered"
    return row


# ------------------------------------------------------------------ state helpers
def w2_state(st: dict) -> dict:
    w = st.get("war2")
    if not isinstance(w, dict):
        w = {}
        st["war2"] = w
    w.setdefault("basis", {})
    w.setdefault("stops", {})
    w.setdefault("history", [])
    return w


def pending(st: dict) -> dict:
    return {norm(s): v for s, v in w2_state(st)["stops"].items() if v.get("status") == "pending"}


def stopped_in_cycle(st: dict, cycle) -> set:
    return {norm(s) for s, v in w2_state(st)["stops"].items() if str(v.get("cycle")) == str(cycle)}


def blocked(st: dict, plan_stops: list, new_rebalance: bool) -> set:
    """names fund_orders must not buy / top up now: every pending stop, tonight's new stops, and (inside a
    cycle) every name stopped in the committed cycle.  On a NEW rebalance the plan's target already left the
    stopped names out, and names stopped in the old cycle may come back through the normal selection."""
    out = set(pending(st)) | {norm(x.get("symbol")) for x in plan_stops or []}
    if not new_rebalance:
        out |= stopped_in_cycle(st, st.get("last_rebalance_session"))
    return out


def merge_plan_stops(st: dict, plan_stops: list, now_tehran: str) -> dict:
    """record tonight's new stops as pending (live runs only).  Returns the war2 state (mutated in st)."""
    w = w2_state(st)
    for x in plan_stops or []:
        s = norm(x.get("symbol"))
        cur = w["stops"].get(s)
        if cur and cur.get("status") == "pending":
            continue
        if cur and cur.get("status") == "sold" and str(cur.get("trigger_session")) == str(x.get("trigger_session")):
            continue                     # same trigger already sold (plan re-read after the sale)
        if cur:
            w["history"].append(cur)
        w["stops"][s] = {k: x.get(k) for k in ("trigger_session", "close", "cost_ps", "stop_price", "adj", "cycle",
                                              "qty", "pnl_pct")}
        w["stops"][s].update(status="pending", recorded_tehran=now_tehran, orders=0)
    w["history"] = w["history"][-HISTORY_MAX:]
    st["war2"] = w
    return w


def mark_sold(st: dict, held: set, now_tehran: str) -> list:
    """pending stops the broker no longer shows -> sold.  Returns the symbols closed now."""
    w = w2_state(st)
    done = []
    for s, v in w["stops"].items():
        if v.get("status") == "pending" and norm(s) not in held:
            v.update(status="sold", sold_seen_tehran=now_tehran)
            done.append(norm(s))
    st["war2"] = w
    return done


def roll_cycle(st: dict, new_cycle) -> None:
    """a new rebalance was committed: sold stops of older cycles move to the history (pending ones stay)."""
    w = w2_state(st)
    keep = {}
    for s, v in w["stops"].items():
        if v.get("status") == "sold" and str(v.get("cycle")) != str(new_cycle):
            w["history"].append(dict(v, symbol=s))
        else:
            keep[s] = v
    w["stops"] = keep
    w["history"] = w["history"][-HISTORY_MAX:]
    st["war2"] = w
