"""WarRobot → dashboard adapter.

The dashboard was reading TeseRobot's own ledger/picks (frozen on 2026-10-20),
which is why the buy and sell tabs showed wrong content. These helpers read
WarRobot's live state instead:

    state/candidates.csv        buy candidates from the nightly scan
    state/ledger.jsonl          the real fills (buy/sell) of this bot
    state/sell_candidates.csv   nightly sell review with its decision
    state/orders_log.jsonl      last order attempts (for the buy tab note)
"""
from __future__ import annotations

import json
from pathlib import Path

WAR = Path(r"C:\Users\Administrator\Desktop\DeepSeek DeskTop\WarRobot")
STATE = WAR / "state"


def _read_csv(name: str):
    fp = STATE / name
    if not fp.exists():
        return []
    import pandas as pd
    try:
        return pd.read_csv(fp).to_dict("records")
    except Exception:
        return []


def _ledger() -> list[dict]:
    fp = STATE / "ledger.jsonl"
    if not fp.exists():
        return []
    rows = []
    for line in fp.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if line:
            try:
                rows.append(json.loads(line))
            except Exception:
                pass
    return rows


def picks_payload() -> dict:
    rows = _read_csv("candidates.csv")
    picks = []
    for i, r in enumerate(rows, start=1):
        close = float(r.get("close") or 0)
        ceiling = float(r.get("next_ceiling") or r.get("ceiling") or 0)
        upside = round((ceiling / close - 1) * 100, 2) if close else 0.0
        score = float(r.get("rank_score") or 0)
        picks.append({
            "rank": int(r.get("pick_rank") or i),
            "symbol": str(r.get("symbol") or ""),
            "score": round(score, 3),
            "feature_source": "queue-lock",
            "degraded": False,
            "dist_to_ceiling_pct": upside,
            "queue_run_today": True,
            "reason": str(r.get("reason") or ""),
        })
    return {
        "picks": picks,
        "generated_at": str(rows[0].get("session") if rows else "-"),
        "for_date_g": str(rows[0].get("session") if rows else "-"),
    }


def _price_map() -> dict:
    out = {}
    for r in _read_csv("sell_candidates.csv"):
        try:
            out[str(r["symbol"])] = float(r.get("last") or 0)
        except Exception:
            pass
    return out


def _broker_positions() -> list[dict]:
    """The broker's real portfolio — the authoritative source for the dashboard."""
    fp = STATE / "broker_portfolio.json"
    if not fp.exists():
        return []
    try:
        d = json.loads(fp.read_text(encoding="utf-8"))
    except Exception:
        return []
    out = []
    for p in d.get("broker", []):
        try:
            qty = int(p.get("qty") or 0)
        except Exception:
            continue
        if qty <= 0:
            continue
        out.append({"symbol": p["symbol"], "qty": qty,
                    "avg": round(float(p.get("avg") or 0), 1),
                    "price": round(float(p.get("last") or 0), 1)})
    return out


def _ledger_avg() -> dict:
    """average cost per symbol from the bot's ledger (fallback for broker avg=0)."""
    out: dict[str, float] = {}
    for r in _ledger():
        sym = r.get("symbol")
        if not sym or r.get("side") != "buy":
            continue
        q, price = float(r.get("qty") or 0), float(r.get("price") or 0)
        b = out.get("__q_" + sym, 0.0)
        out["__q_" + sym] = b + q
        out["__c_" + sym] = out.get("__c_" + sym, 0.0) + q * price
    avg = {}
    for k in list(out):
        if k.startswith("__q_"):
            sym = k[4:]
            q = out[k]
            if q > 0:
                avg[sym] = out.get("__c_" + sym, 0.0) / q
    return avg


def _daily_close(symbol: str) -> float:
    fp = WAR.parent / "TeseRobot" / "data" / "daily" / f"{symbol}_daily.csv"
    try:
        import pandas as pd
        df = pd.read_csv(fp)
        return float(df["Close"].iloc[-1])
    except Exception:
        return 0.0


EXCLUDED = {"خساپا"}          # never shown in the portfolio list or the totals
FUND_SYMBOL = "افران"          # fixed-income fund: money, not an open position


def pnl_at() -> str:
    """When the portfolio P&L snapshot was taken (state/pnl.json)."""
    try:
        return str(json.loads((STATE / "pnl.json").read_text(encoding="utf-8")).get("at") or "")
    except Exception:
        return ""


def weekly() -> dict:
    """Weekly report produced by agents/weekly_report.py."""
    try:
        return json.loads((STATE / "report_weekly.json").read_text(encoding="utf-8"))
    except Exception:
        return {}


def agents() -> dict:
    """Local-agent status, escalation queue and lessons for the agent tab."""
    def _read(p, default):
        try:
            return json.loads(Path(p).read_text(encoding="utf-8"))
        except Exception:
            return default
    latest = _read(STATE / "agents" / "latest.json", {})
    a1 = _read(STATE / "agents" / "a1_login.json", {})
    a2 = _read(STATE / "agents" / "a2_preopen.json", {})
    a3 = _read(STATE / "agents" / "a3_webapp.json", {})
    a4 = _read(STATE / "agents" / "a4_logs.json", {})
    queue = _read(STATE / "escalation" / "queue.json", {"items": []})
    bind_failures = {}
    try:
        bind_failures = json.loads((STATE / "bind_failures.json").read_text(encoding="utf-8"))
    except Exception:
        pass
    order_price = _read(STATE / "agents" / "order_price.json", {})
    slots = _read(STATE / "agents" / "a13_slots.json", {})
    portfolio_pnl = _read(STATE / "agents" / "a14_portfolio_pnl.json", {})
    sold_pnl = _read(STATE / "agents" / "a15_sold_pnl.json", {})
    cash_guard = _read(STATE / "agents" / "a16_cash.json", {})
    lessons = []
    try:
        for line in (STATE / "lessons.jsonl").read_text(encoding="utf-8").splitlines():
            if line.strip():
                lessons.append(json.loads(line))
    except Exception:
        pass
    try:
        import sys as _s
        _s.path.insert(0, r"C:\Users\Administrator\Desktop\DeepSeek DeskTop\WarRobot")
        from agents import llm as _llm
        llm_ok = _llm.healthy()
        llm_busy = _llm._market_hours()
    except Exception:
        llm_ok, llm_busy = False, False
    return {"latest": latest, "a1": a1, "a2": a2, "a3": a3, "a4": a4,
            "escalations": queue.get("items", []), "lessons": lessons[-8:],
            "lessons_count": len(lessons),
            "bind_failures": bind_failures,
            "order_price": order_price,
            "slots": slots,
            "portfolio_pnl": portfolio_pnl,
            "sold_pnl": sold_pnl,
            "cash_guard": cash_guard,
            "llm": {"alive": llm_ok, "paused_market": llm_busy}}


def report() -> dict:
    """Period report payload written by report_periods.py (nightly)."""
    try:
        return json.loads((STATE / "report.json").read_text(encoding="utf-8"))
    except Exception:
        return {"generated_at": "", "bot_start": "", "days_running": 0,
                "periods": {}, "reconciliation": {}}


def to_jalali(ts) -> str:
    """Local timestamp -> Persian calendar in Tehran time (what the trader sees)."""
    if not ts:
        return "—"
    txt = str(ts).strip()
    try:
        from datetime import datetime as _dt
        from zoneinfo import ZoneInfo
        d = _dt.fromisoformat(txt[:19])
        aware = d.astimezone()                      # mark as the server's local zone
        teh = aware.astimezone(ZoneInfo("Asia/Tehran"))
        import jdatetime
        j = jdatetime.date.fromgregorian(date=teh.date())
        if (teh.hour, teh.minute) == (0, 0) and " " not in txt[:19]:
            return f"{j.year:04d}-{j.month:02d}-{j.day:02d}"
        return f"{j.year:04d}-{j.month:02d}-{j.day:02d} {teh.strftime('%H:%M')}"
    except Exception:
        return txt

def sell_data() -> dict:
    """Sell tab payload written by the d6 data agent."""
    try:
        return json.loads((STATE / "webdata" / "sell.json").read_text(encoding="utf-8"))
    except Exception:
        return {}


def webdata() -> dict:
    """Per-card timestamps produced by the data agents (state/webdata/*.json)."""
    out = {}
    for name in ("balance", "portfolio", "pnl", "prices", "sells"):
        try:
            d = json.loads((STATE / "webdata" / f"{name}.json").read_text(encoding="utf-8"))
            out[name] = {"at": d.get("at"), "ok": d.get("ok"),
                         "source": d.get("source")}
        except Exception:
            out[name] = {"at": None, "ok": None, "source": None}
    return out


def balance() -> dict:
    """Buying power + last price refresh, written by the nightly scan."""
    try:
        wd = json.loads((STATE / "webdata" / "balance.json").read_text(encoding="utf-8"))
        if wd.get("account_balance"):
            return {"at": str(wd.get("at") or ""),
                    "cash": float(wd.get("account_balance") or 0),
                    "purchasing_power": float(wd.get("purchasing_power") or 0),
                    "bourse_code": "", "title": ""}
    except Exception:
        pass
    try:
        d = json.loads((STATE / "prices.json").read_text(encoding="utf-8"))
        b = d.get("balance") or {}
        return {"at": str(d.get("at") or ""),
                "cash": float(b.get("account_balance") or 0),
                "purchasing_power": float(b.get("purchasing_power") or 0),
                "bourse_code": b.get("bourse_code") or "",
                "title": b.get("title") or ""}
    except Exception:
        return {"at": "", "cash": 0.0, "purchasing_power": 0.0, "bourse_code": "", "title": ""}


def fund() -> dict:
    """The fixed-income fund (افران) shown separately from the open positions."""
    try:
        d = json.loads((STATE / "pnl.json").read_text(encoding="utf-8"))
        for p in d.get("positions", []):
            if p.get("symbol") == FUND_SYMBOL:
                return {"symbol": FUND_SYMBOL,
                        "qty": p.get("qty"), "value": float(p.get("value") or 0),
                        "pnl_pct": float(p.get("pnl_pct") or 0),
                        "pnl_rial": float(p.get("pnl_rial") or 0),
                        "price": p.get("price"), "avg": p.get("avg_cost")}
    except Exception:
        pass
    return {"symbol": FUND_SYMBOL, "qty": 0, "value": 0.0, "pnl_pct": 0.0,
            "pnl_rial": 0.0, "price": 0, "avg": 0}


def price_map() -> dict:
    """symbol -> {close, session, source} from the nightly snapshot."""
    try:
        d = json.loads((STATE / "prices.json").read_text(encoding="utf-8"))
        return d.get("symbols") or {}
    except Exception:
        return {}


def _pnl_snapshot() -> list[dict]:
    """Positions with the corrected P/L (state/pnl.json built from panel prices)."""
    fp = STATE / "pnl.json"
    if not fp.exists():
        return []
    try:
        d = json.loads(fp.read_text(encoding="utf-8"))
    except Exception:
        return []
    rows = []
    for p in d.get("positions", []):
        if p.get("symbol") in EXCLUDED or p.get("symbol") == FUND_SYMBOL:
            continue
        rows.append({
            "symbol": p["symbol"],
            "qty": int(p["qty"]),
            "avg": float(p.get("avg_cost") or 0),
            "price": float(p.get("price") or 0),
            "value": float(p.get("value") or 0),
            "invested": float(p.get("invested") or 0),
            "pnl_pct": float(p.get("pnl_pct") or 0),
            "total_pct": float(p.get("pnl_pct") or 0),
            "total_pnl": float(p.get("pnl_rial") or 0),
            "avg_source": p.get("avg_source"),
            "status": p.get("status"),
            "to_tp_pp": p.get("to_tp_pp"),
            "to_sl_pp": p.get("to_sl_pp"),
        })
    return rows


def positions() -> list[dict]:

    # single source of truth: the broker panel snapshot (fee-inclusive avg)
    snap = _pnl_snapshot()
    EMPTY_SNAPSHOT_GUARD = True
    try:
        import json as _j
        _raw = _j.loads((STATE / "pnl.json").read_text(encoding="utf-8"))
        if not _raw.get("positions"):
            return []          # no fresh broker snapshot: never fall back to a different source
    except Exception:
        pass
    if snap:
        rows = []
        for x in snap:
            qty = float(x.get("qty") or 0)
            avg = float(x.get("avg") or 0)
            px = float(x.get("price") or 0)
            pct = float(x.get("pnl_pct") or 0)
            rows.append({
                "symbol": x.get("symbol"), "qty": qty, "avg": avg, "price": px,
                "value": round(qty * px, 0), "invested": round(qty * avg, 0),
                "pnl_pct": pct, "total_pct": pct,
                "total_pnl": round(qty * px - qty * avg, 0),
                "status": x.get("status") or "hold",
            })
        return rows
    buys, sells = {}, {}
    for r in _ledger():
        sym, side = r.get("symbol"), r.get("side")
        if not sym:
            continue
        q, p = float(r.get("qty") or 0), float(r.get("price") or 0)
        if side == "buy":
            b = buys.setdefault(sym, {"qty": 0.0, "cost": 0.0})
            b["qty"] += q
            b["cost"] += q * p
        elif side == "sell":
            sells[sym] = sells.get(sym, 0.0) + q
    snap = _pnl_snapshot()
    if snap:
        return snap
    prices = _price_map()
    broker = _broker_positions()
    if broker:
        out = []
        avgs = _ledger_avg()
        for p in broker:
            avg = p["avg"] or avgs.get(p["symbol"]) or 0.0
            px = (p["price"] or prices.get(p["symbol"])
                  or _daily_close(p["symbol"]) or avg)
            value = p["qty"] * px
            invested = p["qty"] * avg
            out.append({
                "symbol": p["symbol"], "qty": p["qty"], "avg": avg, "price": px,
                "value": round(value, 0), "invested": round(invested, 0),
                "pnl_pct": round((px / avg - 1) * 100, 2) if avg else 0.0,
                "total_pct": round((px / avg - 1) * 100, 2) if avg else 0.0,
                "total_pnl": round(value - invested, 0),
            })
        return out
    out = []
    for sym, b in buys.items():
        qty = b["qty"] - sells.get(sym, 0.0)
        if qty <= 0:
            continue
        avg = b["cost"] / b["qty"] if b["qty"] else 0.0
        px = prices.get(sym) or avg
        value = qty * px
        invested = qty * avg
        pnl = value - invested
        out.append({
            "symbol": sym, "qty": int(qty), "avg": round(avg, 1), "price": round(px, 1),
            "value": round(value, 0), "invested": round(invested, 0),
            "pnl_pct": round((px / avg - 1) * 100, 2) if avg else 0.0,
            "total_pct": round((px / avg - 1) * 100, 2) if avg else 0.0,
            "total_pnl": round(pnl, 0),
        })
    return out


def sell_rows() -> list[dict]:
    # SELL_EMPTY_GUARD: never fall back to an older CSV when the broker snapshot is empty
    try:
        import json as _j
        _raw = _j.loads((STATE / "pnl.json").read_text(encoding="utf-8"))
        if not _raw.get("positions"):
            return []
    except Exception:
        pass
    # sell rows come from the same corrected snapshot (panel price + real cost),
    # so the sell tab can never disagree with the positions table again
    snap = _pnl_snapshot()
    if snap:
        return [{
            "symbol": p["symbol"], "qty": p["qty"], "avg": p["avg"],
            "price": p["price"], "pnl_pct": p["pnl_pct"],
            "total_pct": p["pnl_pct"], "decision": p.get("status") or "hold",
            "avg_source": p.get("avg_source"),
        } for p in snap]

    rows = _read_csv("sell_candidates.csv")
    if rows:
        out = []
        for r in rows:
            out.append({
                "symbol": str(r.get("symbol")),
                "qty": int(r.get("qty") or 0),
                "avg": float(r.get("avg_cost") or 0),
                "price": float(r.get("last") or 0),
                "pnl_pct": float(r.get("pnl_pct") or 0),
                "total_pct": float(r.get("pnl_pct") or 0),
                "decision": str(r.get("decision") or "hold"),
            })
        return out
    return [{**p, "decision": "hold"} for p in positions()]


def sells_history(limit: int = 5) -> list[dict]:
    """Recent closed sales — realised.jsonl and the journal are the authority."""
    rows: list[dict] = []
    seen: set[tuple] = set()

    def add(sym, at, qty, avg, price, pnl_rial, pnl_pct):
        key = (str(sym), str(at)[:19])
        if key in seen:
            return
        seen.add(key)
        rows.append({"ts": str(at)[:19], "symbol": str(sym),
                     "qty": float(qty or 0), "avg": float(avg or 0),
                     "price": float(price or 0),
                     "pnl_rial": float(pnl_rial or 0),
                     "pnl_pct": float(pnl_pct or 0)})

    try:
        for line in (STATE / "realized.jsonl").read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            d = json.loads(line)
            qty = float(d.get("qty") or 0)
            price = float(d.get("sell_price") or 0)
            avg = float(d.get("avg_cost") or 0)
            pnl = float(d.get("pnl_rial") or 0)
            pct = float(d.get("pnl_pct") or 0)
            if not pct and avg and price:
                pct = (price / avg - 1) * 100
            add(d.get("symbol"), d.get("at"), qty, avg, price, pnl, pct)
    except Exception:
        pass

    try:
        for line in (STATE / "trade_journal.jsonl").read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            d = json.loads(line)
            if d.get("status") != "closed":
                continue
            add(d.get("symbol"), d.get("sell_at"), d.get("qty"), d.get("price"),
                d.get("sell_price"), d.get("pnl_rial"), d.get("pnl_pct"))
    except Exception:
        pass

    rows.sort(key=lambda r: r["ts"], reverse=True)
    return rows[:limit]

def chart_series() -> list[dict]:
    """Cumulative realised P/L after each sell event."""
    trades = sells_history(limit=200)
    pts, cum = [], 0.0
    for t in trades:
        cum += (t["price"] - t["avg"]) * t["qty"]
        pts.append({"label": t["ts"][:10] or t["symbol"], "value": cum})
    return pts


def chart_points() -> list[dict]:
    """Sell-tab curve: cumulative realised P/L, one point per closed sale.

    It always has a point (the آردینه stop-loss sale) so the chart is never empty,
    and it grows automatically as new sales are journaled.
    """
    pts: list[dict] = []
    try:
        for line in (STATE / "realized.jsonl").read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            d = json.loads(line)
            pts.append({"symbol": d.get("symbol", ""),
                        "ts_fa": to_jalali(d.get("at")),
                        "invested": float(d.get("cost_value") or 0),
                        "proceeds": float(d.get("proceeds") or 0),
                        "pnl_rial": float(d.get("pnl_rial") or 0)})
    except Exception:
        pass
    if not pts:
        try:
            for line in (STATE / "trade_journal.jsonl").read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                d = json.loads(line)
                if d.get("status") != "closed":
                    continue
                pts.append({"symbol": d.get("symbol", ""),
                            "ts_fa": to_jalali(d.get("sell_at")),
                            "invested": float(d.get("price") or 0) * float(d.get("qty") or 0),
                            "proceeds": float(d.get("sell_price") or 0) * float(d.get("qty") or 0),
                            "pnl_rial": float(d.get("pnl_rial") or 0)})
        except Exception:
            pts = []
    pts.sort(key=lambda p: p["ts_fa"])
    return pts

