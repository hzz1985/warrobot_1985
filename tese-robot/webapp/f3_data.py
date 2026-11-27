# -*- coding: utf-8 -*-
"""F3_QVGM view-model for the dashboard (Hark, 2026-11-27).  READ-ONLY.

Everything here is read from files the bot already writes; nothing is written inside WarRobot:
    state/fund_plan.json        nightly plan (21:30 Tehran): target, ranked_top (score + 4 factors), capital
    state/fund_state.json       committed target, last_rebalance_session, legacy V3 note (آباد)
    state/fund_orders.jsonl     every F3 decision / order of fund_orders.py (at_tehran)
    state/fund_run.json         the last fund_orders.py run (prices per name)
    state/orders_log.jsonl      accepted live orders (at_tehran)
    state/jobs_done.json        scheduler: which job ran today and when (Tehran)
    state/webdata/*.json        broker portfolio / buying power (data agents)
    config.json strategy.fund   thresholds (read only)
    scheduler.py                the fundbuy times (parsed, read only)
The funnel counts come from f3_funnel.py (bot caches, read only; result cached in webapp/cache).
"""
from __future__ import annotations

import json
import re
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

WAR = Path(r"C:\Users\Administrator\Desktop\DeepSeek DeskTop\WarRobot")
STATE = WAR / "state"
HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

AFRAN, KHSAPA = "افران", "خساپا"
LEGACY_STOP_PCT = 9.0                 # V3 catastrophe stop (3 x 3 %), exit_engine
LEGACY_EXIT_DEFAULT = "2026-12-14"    # 15-session time exit of the آباد lot (fund_state legacy_note)


# ------------------------------------------------------------------ helpers
def _read(p: Path, default=None):
    try:
        return json.loads(p.read_text(encoding="utf-8-sig"))
    except Exception:
        return {} if default is None else default


def _jsonl(p: Path, last: int = 4000) -> list:
    out = []
    try:
        for line in p.read_text(encoding="utf-8", errors="replace").splitlines()[-last:]:
            try:
                out.append(json.loads(line))
            except Exception:
                pass
    except Exception:
        pass
    return out


def norm(s) -> str:
    return str(s or "").replace("ي", "ی").replace("ك", "ک").replace("\u200c", " ").strip()


def tehran_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=3, minutes=30)


def server_to_tehran(ts) -> str:
    """server-local 'YYYY-MM-DD HH:MM:SS' (Pacific) -> Tehran 'YYYY-MM-DD HH:MM'."""
    try:
        d = datetime.strptime(str(ts)[:19], "%Y-%m-%d %H:%M:%S")
        off = datetime.now() - datetime.now(timezone.utc).replace(tzinfo=None)
        return (d - off + timedelta(hours=3, minutes=30)).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return str(ts or "")


def _f(x, default=None):
    try:
        v = float(x)
        return v if v == v else default
    except Exception:
        return default


def _fund_cfg() -> dict:
    try:
        raw = (json.loads((WAR / "config.json").read_text(encoding="utf-8")).get("strategy") or {}).get("fund") or {}
    except Exception:
        raw = {}
    d = {"enabled": False, "live_orders": False, "variant": "F3_QVGM", "n_hold": 10, "buffer": 20,
         "rebalance_sessions": 21, "pe_max": 40, "afran_share": 1 / 3, "min_trade_rial": 50_000_000,
         "topup_drift": 0.25, "afran_min_excess_pct": 0.02, "manage_afran": True, "mode": "replace",
         "capital_share": 1.0}
    d.update({k: v for k, v in raw.items() if v is not None and not k.startswith("_")})
    return d


# ------------------------------------------------------------------ funnel (background, cached)
_funnel_lock = threading.Lock()
_funnel_state = {"running": False, "error": None}


def _funnel_job():
    try:
        import f3_funnel
        f3_funnel.load()
        _funnel_state["error"] = None
    except Exception as exc:
        _funnel_state["error"] = str(exc)[:200]
    finally:
        _funnel_state["running"] = False


def funnel() -> dict:
    """cached funnel; a stale / missing cache is recomputed in a background thread (page never waits)."""
    try:
        import f3_funnel
        out = _read(f3_funnel.OUT)
        plan_m = f3_funnel.PLAN.stat().st_mtime if f3_funnel.PLAN.exists() else 0
        fresh = f3_funnel.OUT.exists() and f3_funnel.OUT.stat().st_mtime >= plan_m \
            and time.time() - f3_funnel.OUT.stat().st_mtime < 6 * 3600
    except Exception as exc:
        return {"error": str(exc)[:200]}
    if not fresh:
        with _funnel_lock:
            if not _funnel_state["running"]:
                _funnel_state["running"] = True
                threading.Thread(target=_funnel_job, daemon=True).start()
        out = dict(out or {})
        out["refreshing"] = True
    if _funnel_state.get("error"):
        out = dict(out or {})
        out["error"] = _funnel_state["error"]
    return out or {}


# ------------------------------------------------------------------ broker snapshot (same rule as fund_ledger)
def positions_snapshot():
    best_at, best = "", {}
    for p in (STATE / "webdata" / "portfolio.json", STATE / "pnl.json"):
        d = _read(p)
        rows = d.get("positions")
        if not isinstance(rows, list) or not d.get("at") or str(d["at"]) <= best_at:
            continue
        out = {}
        for r in rows:
            s = norm(r.get("symbol"))
            qty = _f(r.get("qty") or r.get("quantity"), 0)
            val = _f(r.get("value"), 0)
            px = _f(r.get("price"), 0) or (val / qty if qty else 0.0)
            if s and qty > 0:
                out[s] = {"qty": qty, "value": val or qty * px, "price": px,
                          "avg": _f(r.get("avg_cost_fee") or r.get("avg_cost") or r.get("avg"), 0),
                          "avg_raw": _f(r.get("avg_cost") or r.get("avg"), 0),
                          "pnl_pct": _f(r.get("pnl_fee_pct") or r.get("pnl_pct"), None)}
        best_at, best = str(d["at"]), out
    return best_at, best


def cash_snapshot():
    cands = []
    b = _read(STATE / "webdata" / "balance.json")
    if b.get("at") and (b.get("purchasing_power") or b.get("account_balance")) is not None:
        cands.append((str(b["at"]), _f(b.get("purchasing_power") or b.get("account_balance"), 0), b.get("ok", True)))
    pr = _read(STATE / "prices.json")
    bal = pr.get("balance") or {}
    if pr.get("at") and bal.get("account_balance") is not None:
        cands.append((str(pr["at"]), _f(bal.get("account_balance"), 0), bal.get("ok", True)))
    if not cands:
        return None, 0.0, False
    at, v, ok = max(cands)
    return at, v, ok


# ------------------------------------------------------------------ schedule
def fund_schedule() -> list:
    """[(HH:MM, job)] for the F3 buy runs, parsed from scheduler.py (read only)."""
    try:
        txt = (WAR / "scheduler.py").read_text(encoding="utf-8")
        rows = re.findall(r'\(\s*"(\d\d:\d\d)"\s*,\s*"(fundbuy\d*)"', txt)
        if rows:
            return sorted(rows)
    except Exception:
        pass
    return [("08:47", "fundbuy"), ("09:05", "fundbuy2"), ("09:40", "fundbuy3"), ("10:20", "fundbuy4"),
            ("11:20", "fundbuy5"), ("12:10", "fundbuy6")]


# ------------------------------------------------------------------ main payload
def payload() -> dict:
    cfg = _fund_cfg()
    plan = _read(STATE / "fund_plan.json")
    st = _read(STATE / "fund_state.json")
    run = _read(STATE / "fund_run.json")
    fun = funnel()
    now_t = tehran_now()
    day = now_t.strftime("%Y-%m-%d")
    hm = now_t.strftime("%H:%M")

    # ---- capital (same formula as fund_ledger.capital, replace mode)
    pos_at, pos = positions_snapshot()
    cash_at, cash, cash_ok = cash_snapshot()
    legacy_names = {norm(s) for s in (st.get("legacy_v3") or [])}
    legacy_held = {s for s in legacy_names if s in pos}
    manual = {AFRAN, KHSAPA}
    fund_pos = {s: p for s, p in pos.items() if s not in manual and s not in legacy_held}
    af = pos.get(AFRAN) or {}
    afran_value = _f(af.get("value"), 0) if cfg.get("manage_afran", True) else 0.0
    fund_value = sum(p["value"] for p in fund_pos.values())
    total = max(0.0, cash) + afran_value + fund_value
    share = float(cfg.get("afran_share") or 0) if cfg.get("manage_afran", True) else 0.0
    afran_target = total * share
    stock_budget = total * (1 - share) * float(cfg.get("capital_share") or 1.0)
    n_hold = int(cfg.get("n_hold") or 10)
    per_name = stock_budget / n_hold if n_hold else 0.0
    excess = afran_value - afran_target
    excess_threshold = max(float(cfg.get("min_trade_rial") or 0), float(cfg.get("afran_min_excess_pct") or 0) * total)
    capital = {
        "at_server": pos_at, "cash": cash, "cash_at_server": cash_at, "cash_ok": cash_ok,
        "total": total, "afran_value": afran_value, "afran_qty": _f(af.get("qty"), 0),
        "afran_price": _f(af.get("price"), 0), "afran_pct": (afran_value / total * 100) if total else 0.0,
        "afran_target": afran_target, "afran_target_pct": share * 100, "afran_excess": excess,
        "afran_excess_units": int(excess // af["price"]) if excess > 0 and _f(af.get("price"), 0) else 0,
        "afran_excess_due": excess >= excess_threshold, "excess_threshold": excess_threshold,
        "stock_budget": stock_budget, "stock_pct": (1 - share) * 100, "fund_value": fund_value,
        "fund_fill_pct": (fund_value / stock_budget * 100) if stock_budget else 0.0, "per_name": per_name,
    }

    # ---- today's orders (Tehran day)
    olog_today = [r for r in _jsonl(STATE / "fund_orders.jsonl") if str(r.get("at_tehran") or "")[:10] == day]
    live_today = [r for r in _jsonl(STATE / "orders_log.jsonl") if str(r.get("at_tehran") or "")[:10] == day]
    ev_by = {}
    for r in olog_today:
        if r.get("event") == "order" and r.get("symbol"):
            ev_by.setdefault(norm(r["symbol"]), []).append(r)
    live_by = {}
    for r in live_today:
        if r.get("symbol") and r.get("submitted"):
            live_by.setdefault(norm(r["symbol"]), []).append(r)
    run_buys = {norm(b.get("symbol")): b for b in ((run.get("plan") or {}).get("buys") or [])}

    def order_status(sym: str, held_qty: float) -> dict:
        evs = [e for e in ev_by.get(sym, []) if str(e.get("side") or "buy") == "buy"]
        live = [e for e in live_by.get(sym, []) if str(e.get("side") or "buy") == "buy"]
        sub = [e for e in evs if e.get("submitted") and not e.get("dry")] or live
        last = (sub or evs or [None])[-1]
        if sub:
            want = sum(_f(e.get("qty"), 0) for e in sub)
            if held_qty and want and held_qty >= 0.98 * want:
                return {"code": "filled", "fa": "پر شد", "last": last}
            if held_qty:
                return {"code": "partial", "fa": "بخشی پر شد", "last": last}
            return {"code": "queued", "fa": "ثبت شد / در صف", "last": last}
        if evs:
            if all(e.get("dry") for e in evs):
                return {"code": "dry", "fa": "آزمایشی (بدون ارسال)", "last": last}
            return {"code": "failed", "fa": "ثبت نشد", "last": last, "msg": str(last.get("message") or "")[:60]}
        if held_qty:
            return {"code": "held", "fa": "در سبد", "last": None}
        return {"code": "none", "fa": "بدون سفارش", "last": None}

    # ---- picks table
    ranked = {norm(r.get("sym")): r for r in (plan.get("ranked_top") or [])}
    rank_of = fun.get("rank_of") or {}
    target = [norm(s) for s in (plan.get("target") or st.get("target") or [])]
    picks = []
    for i, s in enumerate(target, 1):
        r = ranked.get(s) or {}
        p = fund_pos.get(s) or {}
        held_qty, held_val = _f(p.get("qty"), 0), _f(p.get("value"), 0)
        ey = _f(r.get("ey"))
        stt = order_status(s, held_qty)
        last = stt.get("last") or {}
        rb = run_buys.get(s) or {}
        lim_kind = "order" if _f(last.get("price")) else ("run" if _f(rb.get("price")) else "ref")
        lim = _f(last.get("price")) or _f(rb.get("price"))
        lim_src = last.get("price_source") or rb.get("price_source") or ("ref" if not lim else "")
        if not lim:
            lim = _f(r.get("close")) or _f(next((b.get("ref_price") for b in plan.get("buys") or []
                                                if norm(b.get("symbol")) == s), None))
        picks.append({
            "symbol": s, "rank": r.get("rank") or rank_of.get(s) or i, "score": _f(r.get("score")),
            "ey": ey, "yoy": _f(r.get("yoy")), "roe": _f(r.get("roe")), "mom": _f(r.get("mom")),
            "pe": (1 / ey) if ey and ey > 0 else None, "close": _f(r.get("close")), "pub": r.get("pub"),
            "target": per_name, "held_qty": held_qty, "held_value": held_val,
            "gap": per_name - held_val, "gap_pct": ((per_name - held_val) / per_name * 100) if per_name else 0,
            "limit": lim, "limit_src": lim_src, "limit_kind": lim_kind, "status": stt["code"], "status_fa": stt["fa"],
            "status_msg": stt.get("msg") or "", "order_qty": _f(last.get("qty")),
            "order_at": str(last.get("at_tehran") or "")[11:16],
        })

    # ---- hold buffer (held fund names, rank 11..buffer) + held names outside the buffer
    buffer = int(cfg.get("buffer") or 20)
    tset = set(target)
    hold_buffer, out_of_buffer = [], []
    for s, p in sorted(fund_pos.items()):
        if s in tset:
            continue
        rk = rank_of.get(s) or (ranked.get(s) or {}).get("rank")
        row = {"symbol": s, "rank": rk, "value": p["value"], "qty": p["qty"]}
        (hold_buffer if rk and rk <= buffer else out_of_buffer).append(row)
    near = [{"symbol": norm(r.get("sym")), "rank": r.get("rank"), "score": _f(r.get("score")),
             "held": norm(r.get("sym")) in fund_pos}
            for r in (plan.get("ranked_top") or []) if n_hold < int(r.get("rank") or 0) <= buffer]

    # ---- rebalance countdown
    reb_n = int(cfg.get("rebalance_sessions") or 21)
    last_reb = st.get("last_rebalance_session")
    since = plan.get("sessions_since_rebalance")
    if last_reb:
        since_n = int(since or 0)
        reb = {"first": False, "since": since_n, "left": max(0, reb_n - since_n), "of": reb_n,
               "last_session": str(last_reb), "last_at": st.get("target_committed_tehran")}
    else:
        reb = {"first": True, "since": 0, "left": reb_n, "of": reb_n, "last_session": None,
               "pending_session": plan.get("session")}

    # ---- buy schedule with status
    jd = (_read(STATE / "jobs_done.json") or {}).get(day) or {}
    sched = []
    slots = fund_schedule()
    for k, (at, job) in enumerate(slots):
        nxt = slots[k + 1][0] if k + 1 < len(slots) else "23:59"
        evs = [e for e in olog_today if at <= str(e.get("at_tehran") or "")[11:16] < nxt]
        orders = [e for e in evs if e.get("event") == "order"]
        nsub = sum(1 for e in orders if e.get("submitted") and not e.get("dry"))
        skipped = next((e.get("reason") for e in evs if e.get("event") == "skipped"), None)
        done_at = jd.get(job)
        if str(done_at or "").startswith("closed@"):
            code, fa = "closed", "بازار بسته — بدون سفارش"
        elif done_at:
            code, fa = "done", f"اجرا شد {done_at}"
        elif hm >= at:
            code, fa = ("running", "در حال اجرا / ثبت نشده") if hm < nxt else ("missed", "اجرا ثبت نشد")
        else:
            code, fa = "wait", "در انتظار"
        sched.append({"at": at, "job": job, "code": code, "fa": fa, "orders": len(orders), "submitted": nsub,
                      "skipped": skipped})
    pre = [("07:50", "ورود و همگام‌سازی ساعت", "clock"), ("08:27", "تمرین صبح (بدون سفارش)", "rehearse")]
    pre_rows = [{"at": a, "label": l, "done": jd.get(j),
                 "closed": str(jd.get(j) or "").startswith("closed@")} for a, l, j in pre]

    # ---- legacy / manual
    legacy = []
    note = st.get("legacy_note") or ""
    m = re.search(r"time exit\D{0,12}(20\d\d-\d\d-\d\d)", note)       # "15-session time exit 2026-12-14"
    exit_day = m.group(1) if m else LEGACY_EXIT_DEFAULT
    for s in sorted(legacy_names):
        p = pos.get(s)
        if not p:
            legacy.append({"symbol": s, "held": False})
            continue
        avg = p.get("avg_raw") or p.get("avg") or 0
        stop_px = avg * (1 - LEGACY_STOP_PCT / 100) if avg else None
        try:
            days_left = (datetime.strptime(exit_day, "%Y-%m-%d").date() - now_t.date()).days
        except Exception:
            days_left = None
        legacy.append({"symbol": s, "held": True, "qty": p["qty"], "value": p["value"], "price": p["price"],
                       "avg": avg, "pnl_pct": p.get("pnl_pct") if p.get("pnl_pct") is not None else
                       ((p["price"] / avg - 1) * 100 if avg else None),
                       "stop_pct": LEGACY_STOP_PCT, "stop_px": stop_px,
                       "to_stop_pct": ((p["price"] / stop_px - 1) * 100) if stop_px else None,
                       "exit_day": exit_day, "exit_days_left": days_left})
    kh = pos.get(KHSAPA)
    manual_rows = [{"symbol": KHSAPA, "qty": kh["qty"], "value": kh["value"], "pnl_pct": kh.get("pnl_pct")}] if kh else []

    # ---- afran sale today
    af_today = [e for e in ev_by.get(AFRAN, []) if str(e.get("side") or "") == "sell"]
    af_status = None
    if af_today:
        e = af_today[-1]
        af_status = {"at": str(e.get("at_tehran") or "")[11:16], "qty": e.get("qty"), "price": e.get("price"),
                     "submitted": bool(e.get("submitted")) and not e.get("dry"), "dry": bool(e.get("dry")),
                     "message": str(e.get("message") or "")[:60]}

    notes = list(plan.get("notes") or [])
    return {
        "ok": bool(plan), "cfg": cfg, "variant": plan.get("variant") or cfg.get("variant"),
        "live_orders": bool(cfg.get("live_orders")), "enabled": bool(cfg.get("enabled")),
        "plan_at_server": plan.get("at"), "plan_at_tehran": server_to_tehran(plan.get("at")),
        "plan_session": plan.get("session"), "plan_action": plan.get("action"), "plan_notes": notes,
        "price_coverage": plan.get("price_coverage"), "fund_coverage": plan.get("fund_coverage"),
        "funnel": fun, "capital": capital, "picks": picks, "hold_buffer": hold_buffer,
        "out_of_buffer": out_of_buffer, "near": near, "rebalance": reb, "schedule": sched, "pre": pre_rows,
        "legacy": legacy, "manual": manual_rows, "afran_sale": af_status, "today": day, "now_hm": hm,
        "orders_today": len([e for e in olog_today if e.get("event") == "order"]),
        "submitted_today": len([e for e in olog_today if e.get("event") == "order" and e.get("submitted")
                                and not e.get("dry")]),
        "last_run": {"started": run.get("started_tehran"), "finished": run.get("finished_tehran"),
                     "dry": run.get("dry")},
    }


def safe_payload() -> dict:
    try:
        return payload()
    except Exception as exc:          # the dashboard must never 500 because of this panel
        return {"ok": False, "error": str(exc)[:300]}


if __name__ == "__main__":
    d = safe_payload()
    f = d.get("funnel") or {}
    f.pop("rank_of", None)
    f.pop("ranked_top", None)
    print(json.dumps(d, ensure_ascii=False, indent=1, default=str)[:20000])
