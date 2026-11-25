# -*- coding: utf-8 -*-
"""TeseRobot dashboard — two tabs: buy candidates and sell candidates.

Data is read from the robot's own state, so the page can never disagree with what
the bot actually did:
    data/today_picks.json     -> buy candidates (queue_v3)
    state/ledger.jsonl        -> open positions + realised sells
    state/orders.jsonl        -> today's order intents
    reports/close_*.json      -> last close snapshot

The sell tab also draws a red line chart of realised profit % per closed trade
(exit vs. average cost), which is the portfolio-performance measure requested.
"""
from __future__ import annotations

import json
import subprocess
import uuid
import numpy as np
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

import pandas as pd
from flask import Flask, render_template, jsonify, request

BASE = Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0, str(BASE / "src"))

from tsebot import config as config_mod  # noqa: E402
from tsebot.ledger import Ledger  # noqa: E402
from tsebot.calendar_ir import now_tehran  # noqa: E402

import warrobot_data as Wdata
app = Flask(__name__)
CFG = config_mod.load()


@app.after_request
def _no_store(resp):
    """Never let a browser cache the dashboard.

    Desktop Chrome and mobile Chrome were showing different P&L simply because
    one of them had an old page cached: the numbers are server-rendered, so a
    cached copy is frozen in time.
    """
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    resp.headers["Pragma"] = "no-cache"
    return resp


@app.before_request
def _access_log():
    """Log the client and User-Agent so a 'page does not load' report can be
    matched against what actually reached the server."""
    if request.path in ("/health", "/favicon.ico"):
        return
    try:
        print(f"[req] ip={request.remote_addr} {request.method} {request.path} "
              f"host={request.host} ua={request.user_agent.string[:110]}", flush=True)
    except Exception:
        pass


@app.route("/favicon.ico")
def favicon():
    return ("", 204)


@app.route("/simple")
def simple():
    """Template-free fallback page: proves the server is reachable even if a
    browser extension or cache breaks the main page."""
    snap = {
        "ok": True,
        "time": now_tehran().isoformat(timespec="seconds"),
        "mode": CFG.mode,
        "host": request.host,
        "positions": len(open_positions()),
        "picks_for": _read_json(CFG.path("picks_dir") / "today_picks.json", {}).get("for_date_g"),
    }
    body = json.dumps(snap, ensure_ascii=False, indent=2)
    return ("<!doctype html><meta charset='utf-8'><title>simple</title>"
            "<body style='font-family:monospace;background:#111;color:#eee;padding:20px'>"
            "<h3>TeseRobot — simple check</h3><pre>" + body + "</pre>"
            "<p><a style='color:#7ee2a8' href='/'>main page</a></p></body>")


def _read_json(p: Path, default: Any):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return default


def _ledger() -> Ledger:
    return Ledger(Path(r"C:\Users\Administrator\Desktop\DeepSeek DeskTop\WarRobot\state") / "ledger_all.jsonl")


def realised_series() -> List[Dict[str, Any]]:
    """Replay fills and record every sell's profit % against average cost."""
    led = _ledger()
    qty: Dict[str, float] = {}
    cost: Dict[str, float] = {}
    out: List[Dict[str, Any]] = []
    # replay in TIME order: the ledger is append-only, so a buy can sit in the file
    # after the sell it belongs to (that is why older sells used to have no basis)
    for ev in sorted(led.events(), key=lambda e: str(e.get("ts") or "")):
        if not ev.get("side"):
            continue
        if ev.get("kind") != "fill":
            continue
        sym = ev.get("symbol")
        q = float(ev.get("qty") or 0)
        px = float(ev.get("price") or 0)
        fee = float(ev.get("fee") or 0)
        if ev.get("side") == "buy":
            qty[sym] = qty.get(sym, 0) + q
            cost[sym] = cost.get(sym, 0) + q * px + fee
        elif ev.get("side") == "sell" and qty.get(sym, 0) > 0:
            avg = cost[sym] / qty[sym]
            sold = min(q, qty[sym])
            pnl = (px - avg) / avg * 100.0 if avg else 0.0
            pnl_rial = (px - avg) * sold - fee
            out.append({
                "ts": str(ev.get("ts") or "")[:16].replace("T", " "),
                "symbol": sym,
                "qty": int(sold),
                "avg": round(avg, 2),
                "price": px,
                "pnl_pct": round(pnl, 2),
                "pnl_rial": round(pnl_rial, 0),
            })
            cost[sym] -= avg * sold
            qty[sym] -= sold
        elif ev.get("side") == "sell":
            # a sell without a matching buy in the ledger (position opened before the
            # ledger existed): fall back to the broker cost basis so the point is not
            # silently dropped from the chart
            avg = _cost_basis().get(sym)
            if avg:
                pnl = (px - avg) / avg * 100.0
            else:
                # no cost basis anywhere: keep the point (so the curve is continuous)
                # and flag it instead of silently dropping the trade
                avg, pnl = 0.0, 0.0
            out.append({"ts": str(ev.get("ts") or "")[:16].replace("T", " "),
                        "symbol": sym, "qty": int(q), "avg": round(avg, 2),
                        "price": px, "pnl_pct": round(pnl, 2),
                        "pnl_rial": round((px - avg) * q - fee, 0) if avg else 0, "fallback": avg == 0.0})
    return out




def current_picks() -> Dict[str, Any]:
    """Tonight's ride candidates, with the entry distance and lock flag.

    Falls back to the legacy picks file when the nightly scan has not run yet.
    """
    path = Path(r"C:\Users\Administrator\Desktop\DeepSeek DeskTop\WarRobot\state") / "candidates.csv"
    if path.exists():
        try:
            import pandas as _pd
            df = _pd.read_csv(path)
            rows = []
            for i, r in df.iterrows():
                close = float(r.get("close") or 0)
                ceil = float(r.get("next_ceiling") or r.get("ceiling_today") or 0)
                dist = ((ceil - close) / close * 100) if close else 0.0
                locked = int(close >= ceil * 0.999) if ceil else 0
                rows.append({"rank": i + 1, "symbol": str(r["symbol"]),
                             "score": float(r.get("prob") or 0),
                             "feature_source": "ride_lock", "degraded": False,
                             "dist_to_ceiling_pct": round(max(dist, 0.0), 3),
                             "queue_run_today": locked,
                             "reason": str(r.get("reason") or "BFomul")})
            if rows:
                return {"generated_at": str(df["built_at"].iloc[0]) if "built_at" in df else "-",
                        "for_date_g": "next", "picks": rows, "source": "ride_locked"}
        except Exception:
            pass
    return _read_json(CFG.path("picks_dir") / "today_picks.json", {})




PROMPT_FILE = Path(__file__).resolve().parents[1] / "state" / "prompt_inbox.jsonl"

SAFE_ACTIONS = {
    "status":  [sys.executable, "scripts/readiness_check.py"],
    "scan":    [sys.executable, "-c",
                "import sys; sys.path.insert(0,'src'); from tsebot import config, ride_locked; "
                "cfg=config.load(); ride_locked.write_candidates(cfg); print('scan done')"],
    "report":  [sys.executable, "scripts/daily_report.py"],
    "restart-web": ["pm2", "restart", "tse-web"],
}


def _prompt_rows(limit: int = 50) -> List[Dict[str, Any]]:
    if not PROMPT_FILE.exists():
        return []
    rows = []
    for line in PROMPT_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                rows.append(json.loads(line))
            except Exception:
                continue
    return rows[-limit:]


def _prompt_append(rec: Dict[str, Any]) -> None:
    PROMPT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with PROMPT_FILE.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")

EXPORT_FILE = Path(__file__).resolve().parents[1] / "state" / "deepseek_export.json"


def _load_export() -> List[Dict[str, Any]]:
    try:
        return json.loads(EXPORT_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []


def _save_export(items: List[Dict[str, Any]]) -> None:
    EXPORT_FILE.parent.mkdir(parents=True, exist_ok=True)
    EXPORT_FILE.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")


def to_jalali(ts: str) -> str:
    try:
        import jdatetime
        y, m, d = (int(x) for x in str(ts)[:10].split("-"))
        j = jdatetime.date.fromgregorian(year=y, month=m, day=d)
        return f"{j.year:04d}/{j.month:02d}/{j.day:02d}"
    except Exception:
        return str(ts)[:10]


def last_symbols(rows: List[Dict[str, Any]], n: int = 5) -> List[Dict[str, Any]]:
    """Rows of the last N SOLD SYMBOLS (all their sell orders included).

    The history table must not be truncated by order count: one symbol can have
    several sell tickets and all of them belong to the same symbol block.
    """
    order = []
    for r in rows:
        sym = r.get("symbol")
        if sym not in order:
            order.append(sym)
    keep = set(order[-n:]) if n > 0 else set(order)
    return [r for r in rows if r.get("symbol") in keep]


def per_symbol_series(limit: int = 0) -> List[Dict[str, Any]]:
    """One row per sold symbol: invested rial -> proceeds rial -> realised P&L.

    P&L is literally (money received) - (money paid) for the quantity sold, so a
    20M purchase sold for 22M shows +2M on the curve.
    """
    led = _ledger()
    lots: Dict[str, List[Dict[str, float]]] = {}
    rows: Dict[str, Dict[str, Any]] = {}
    for ev in sorted(led.events(), key=lambda e: str(e.get("ts") or "")):
        if ev.get("kind") != "fill" or not ev.get("side"):
            continue
        sym = ev.get("symbol")
        q = float(ev.get("qty") or 0)
        px = float(ev.get("price") or 0)
        fee = float(ev.get("fee") or 0)
        if ev.get("side") == "buy":
            lots.setdefault(sym, []).append({"q": q, "unit": px + (fee / q if q else 0.0)})
        elif ev.get("side") == "sell" and lots.get(sym):
            remaining, invested = q, 0.0
            while remaining > 0 and lots[sym]:
                lot = lots[sym][0]
                take = min(remaining, lot["q"])
                invested += take * lot["unit"]
                lot["q"] -= take
                remaining -= take
                if lot["q"] <= 1e-9:
                    lots[sym].pop(0)
            sold = q - remaining
            if sold <= 0:
                continue
            proceeds = sold * px - fee
            r = rows.setdefault(sym, {"symbol": sym, "qty": 0.0, "invested": 0.0,
                                      "proceeds": 0.0, "ts": ""})
            r["qty"] += sold
            r["invested"] += invested
            r["proceeds"] += proceeds
            r["ts"] = str(ev.get("ts") or "")[:16].replace("T", " ")
    out = []
    for r in rows.values():
        if r["qty"] <= 0 or r["invested"] <= 0:
            continue
        # authoritative realised P&L comes from the ledger (weighted average cost,
        # both fees included); the lot matching above is only for the tooltip fields
        try:
            pos = led.positions.get(r["symbol"])
            pnl = float(getattr(pos, "realized", 0) or 0) if pos is not None else (r["proceeds"] - r["invested"])
        except Exception:
            pnl = r["proceeds"] - r["invested"]
        invested = float(r["invested"])
        proceeds = invested + pnl          # keeps bought/sold/P&L internally consistent
        out.append({"symbol": r["symbol"], "ts": r["ts"], "ts_fa": to_jalali(r["ts"]),
                    "qty": int(r["qty"]), "invested": round(invested, 0),
                    "proceeds": round(proceeds, 0),
                    "avg": round(invested / r["qty"], 1),
                    "price": round(proceeds / r["qty"], 1),
                    "pnl_rial": round(pnl, 0),
                    "pnl_pct": round((r["proceeds"] / r["invested"] - 1) * 100, 2)})
    out.sort(key=lambda x: x["ts"])
    return out[-limit:] if limit else out



def _toman(v: float) -> str:
    t = v / 10.0
    a = abs(t)
    if a >= 1e9:
        return f"{t/1e9:,.2f}B"
    if a >= 1e6:
        return f"{t/1e6:,.2f}M"
    if a >= 1e3:
        return f"{t/1e3:,.0f}K"
    return f"{t:,.0f}"


def _step_for(limit: float, n: int = 4) -> float:
    raw = max(limit, 1.0) / n
    mag = 10 ** np.floor(np.log10(raw)) if raw > 0 else 1.0
    for m in (1, 2, 2.5, 5, 10):
        if m * mag >= raw:
            return m * mag
    return 10 * mag


def svg_chart(points: List[Dict[str, Any]], width: int = 1000, height: int = 400) -> str:
    """Cumulative portfolio P&L with readable, non-overlapping labels."""
    if not points:
        return "<div class='empty'>هنوز داده‌ای برای رسم منحنی نیست.</div>"

    PAD_L, PAD_R, PAD_T, PAD_B = 118, 34, 46, 96
    w = width - PAD_L - PAD_R
    h = height - PAD_T - PAD_B

    cum = [0.0]
    for p in points:
        cum.append(cum[-1] + float(p.get("pnl_rial") or 0))
    peak = max(abs(v) for v in cum) or 1.0
    step = _step_for(peak, 4)
    limit = step * 4

    def x(i: int) -> float:
        return PAD_L + (w * i / max(1, len(cum) - 1))

    def y(v: float) -> float:
        return PAD_T + h * (1 - (v + limit) / (2 * limit))

    def clamp_x(v: float) -> float:
        return min(max(v, PAD_L + 26), width - PAD_R - 26)

    def clamp_y(v: float) -> float:
        return min(max(v, PAD_T + 14), height - PAD_B - 4)

    out = [f"<svg viewBox='0 0 {width} {height}' class='chart' role='img' "
           f"preserveAspectRatio='xMidYMid meet'>"]

    # grid + money labels
    v = -limit
    while v <= limit + 1e-6:
        yy = y(v)
        cls = "zero" if abs(v) < 1e-9 else "grid"
        out.append(f"<line x1='{PAD_L}' y1='{yy:.1f}' x2='{width-PAD_R}' y2='{yy:.1f}' class='{cls}'/>")
        out.append(f"<text x='{PAD_L-10}' y='{yy+4:.1f}' class='axis' text-anchor='end' "
                   f"direction='ltr' unicode-bidi='plaintext'>{_toman(v)}</text>")
        v += step

    # profit / loss zones
    out.append(f"<text x='{width-PAD_R}' y='{y(limit*0.78):.1f}' class='zone' text-anchor='start'>سود</text>")
    out.append(f"<text x='{width-PAD_R}' y='{y(-limit*0.78):.1f}' class='zone' text-anchor='start'>ضرر</text>")

    # the curve
    pts = " ".join(f"{x(i):.1f},{y(val):.1f}" for i, val in enumerate(cum))
    out.append(f"<polyline points='{pts}' class='line'/>")

    # points: one label each, alternating sides, clamped inside the box
    labelled = []
    for i, p in enumerate(points):
        val = cum[i + 1]
        cx, cy = x(i + 1), y(val)
        colour = "#14804a" if val >= 0 else "#c62828"
        out.append(f"<circle cx='{cx:.1f}' cy='{cy:.1f}' r='5.5' fill='{colour}'/>")
        above = (i % 2 == 0) if val >= 0 else (i % 2 != 0)
        name = str(p.get("symbol") or "").strip()
        ly = clamp_y(cy - 26 if above else cy + 34)
        lx = clamp_x(cx)
        out.append(f"<text x='{lx:.1f}' y='{ly:.1f}' class='point-label' text-anchor='middle'>{name}</text>")
        out.append(f"<text x='{lx:.1f}' y='{ly + (-15 if above else 15):.1f}' class='point-value' "
                   f"text-anchor='middle'>{_toman(float(p.get('pnl_rial') or 0))}</text>")
        out.append(f"<title>{name} | خرید {_toman(p.get('invested'))} · فروش {_toman(p.get('proceeds'))} "
                   f"| سود {_toman(float(p.get('pnl_rial') or 0))} | جمع {_toman(val)} | {p.get('ts_fa', '')}</title>")
        labelled.append((i, cx, str(p.get("ts_fa") or "")))

    # x axis: only when the caption is not already the point name
    names = {str(p.get("symbol") or "") for p in points}
    dates = {str(p.get("ts_fa") or "") for p in points}
    show_axis = not dates.issubset(names)          # true only for a real time series
    every = max(1, (len(labelled) + 7) // 8)
    for i, cx, date in labelled:
        if not show_axis or i % every:
            continue
        out.append(f"<text x='{clamp_x(cx):.1f}' y='{height-PAD_B+20:.0f}' class='axis' "
                   f"text-anchor='middle'>{date}</text>")

    # legend
    total = cum[-1]
    out.append(f"<text x='{width-PAD_R}' y='{PAD_T-16:.0f}' class='legend-main' text-anchor='start'>جمع سود/زیان: {_toman(total)} تومان</text>")
    out.append(f"<text x='{width-PAD_R}' y='{height-10:.0f}' class='axis' text-anchor='start'>"
               f"محور عمودی: سود/زیان تجمعی (تومان) · خط‌چین: صفر</text>")
    out.append("</svg>")
    return "".join(out)

def _norm(sym: str) -> str:
    """Strip board suffixes so "شپاس1" and "شپاس" map to the same position."""
    s = str(sym or "").strip()
    while s and (s[-1].isdigit() or s[-1] in "ح"):
        s = s[:-1]
    return s


def _marks() -> Dict[str, Dict[str, Any]]:
    """Live prices written by the engine on every scan (state/marks.json)."""
    data = _read_json(CFG.path("state_dir") / "marks.json", {})
    return {_norm(k): v for k, v in (data or {}).items() if isinstance(v, dict)}


def live_prices() -> Dict[str, float]:
    out: Dict[str, float] = {}
    for sym, m in _marks().items():
        try:
            v = float(m.get("last") or 0)
        except Exception:
            v = 0.0
        if v > 0:
            out[sym] = v
    return out


def _fetch_live_once() -> Dict[str, float]:
    """One refresh attempt; the engine owns the real source, so this is best-effort."""
    return live_prices()


def _refresh_live_forever() -> None:
    import time as _t
    while True:
        try:
            _fetch_live_once()
        except Exception:
            pass
        _t.sleep(60)


def _last_close(symbol: str) -> float | None:
    """Latest price: live book (TSETMC), else the newest daily close found on disk."""
    # 0) the broker portfolio grid is the most authoritative live price
    try:
        import sys as _s2
        v3 = r"C:\Users\Administrator\Desktop\DeepSeek DeskTop\TseRobotV3"
        if v3 not in _s2.path:
            _s2.path.insert(0, v3)
        import broker_portfolio as _bp
        px = _bp.last_prices(ttl=60).get(str(symbol))
        if px:
            return float(px)
    except Exception:
        pass
    # 1) live book
    try:
        import sys as _s
        of = r"C:\Users\Administrator\Desktop\DeepSeek DeskTop\Tse2\orderflow"
        if of not in _s.path:
            _s.path.insert(0, of)
        import tsetmc as _api
        inst = _api.resolve(str(symbol))
        if inst:
            b = _api.parse_book(_api.best_limits(inst["ins_code"]))
            px = float(b.get("best_bid") or b.get("mid") or 0)
            if px > 0:
                return px
    except Exception:
        pass
    # 2) daily files in every known location
    try:
        import pandas as _pd
        from pathlib import Path as _P
        base = _P(r"C:\Users\Administrator\Desktop\DeepSeek DeskTop")
        for cand in (base / "TeseRobot" / "data" / "daily" / f"{symbol}_daily.csv",
                     base / "Tse2" / "orderflow" / "data" / "daily3y" / f"{symbol}.csv",
                     CFG.path("data_dir") / "daily" / f"{symbol}_daily.csv"):
            if cand.exists():
                d = _pd.read_csv(cand, dtype={"date": str})
                if len(d):
                    return float(d.sort_values("date")["Close"].iloc[-1])
    except Exception:
        pass
    return None


def _cost_basis() -> Dict[str, float]:
    data = _read_json(CFG.path("state_dir") / "cost_basis.json", {})
    return {_norm(k): float(v) for k, v in (data or {}).items() if v}


def open_positions() -> List[Dict[str, Any]]:
    """Open positions from the ledger, marked to the latest known price."""
    led = _ledger()
    prices = live_prices()
    basis = _cost_basis()
    out: List[Dict[str, Any]] = []
    for pos in led.open_positions():
        sym = _norm(pos.symbol)
        qty = float(pos.qty)
        avg = float(pos.avg_cost or basis.get(sym) or 0.0)
        cost = qty * avg
        last = prices.get(sym) or _last_close(pos.symbol) or avg
        pnl_pct = ((last / avg) - 1) * 100 if avg else 0.0
        out.append({
            "symbol": pos.symbol, "qty": int(qty), "avg": round(avg, 1),
            "cost": round(cost, 0), "invested": round(cost, 0), "price": round(float(last), 1),
            "value": round(float(last) * qty, 0),
            "pnl_pct": round(pnl_pct, 2),
            "total_pnl": round(float(last - avg) * qty, 0),
            "total_pct": round(pnl_pct, 2),
            "share_pct": 0.0,
        })
    total = sum(p["value"] for p in out) or 1.0
    for p in out:
        p["share_pct"] = round(p["value"] / total * 100, 1)
    return out


def decide_label(pnl: float, total: float, cfg) -> str:
    """What the bot would do with this position right now."""
    try:
        target = float(cfg.get("sell.target_pct", 6.0))
        stop = float(cfg.get("sell.stop_pct", 3.0))
    except Exception:
        target, stop = 6.0, 3.0
    if total <= -stop or pnl <= -stop:
        return "فروش (حد ضرر)"
    if total >= target or pnl >= target:
        return "فروش (حد سود)"
    return "نگهداری"


def health():
    return jsonify({"ok": True, "mode": CFG.mode, "time": now_tehran().isoformat(timespec="seconds")})






@app.route("/export/add", methods=["POST"])
def export_add():
    data = request.get_json(force=True, silent=True) or {}
    content = str(data.get("content") or "")
    if not content.strip():
        return jsonify({"ok": False, "error": "empty"}), 400
    items = _load_export()
    items.append({"id": uuid.uuid4().hex[:12],
                  "ts": now_tehran().strftime("%Y-%m-%d %H:%M:%S"),
                  "title": str(data.get("title") or "پاسخ"),
                  "content": content})
    _save_export(items)
    return jsonify({"ok": True, "id": items[-1]["id"]})


@app.route("/export/delete", methods=["POST"])
def export_delete():
    data = request.get_json(force=True, silent=True) or {}
    target = str(data.get("id") or "")
    items = [i for i in _load_export() if i.get("id") != target]
    _save_export(items)
    return jsonify({"ok": True, "count": len(items)})


def _alloc_advice():
    """Shadow allocation advice written by the bot (no trades behind it)."""
    import json
    from pathlib import Path as _P
    fp = _P(r"C:\Users\Administrator\Desktop\DeepSeek DeskTop\WarRobot\state\alloc_advice.json")
    try:
        return json.loads(fp.read_text(encoding="utf-8"))
    except Exception:
        return {}




def _web_snapshot():
    """Everything the nightly refresh wrote: candidates, sells, positions, orders."""
    import json
    from pathlib import Path as _P
    fp = _P(r"C:\Users\Administrator\Desktop\DeepSeek DeskTop\WarRobot\state\web_snapshot.json")
    try:
        return json.loads(fp.read_text(encoding="utf-8"))
    except Exception:
        return {}


@app.route("/")
def index():
    snap = _web_snapshot()
    picks = Wdata.picks_payload()
    advice = _alloc_advice()
    exports = list(reversed(_load_export()))[:20]   # newest 20 only (page size)
    prompts = list(reversed(_prompt_rows(30)))
    excluded = set(CFG.get("portfolio.exclude_symbols") or [])
    positions = Wdata.positions()   # every broker position is shown
    sells = Wdata.sells_history(5)      # last 5 SOLD SYMBOLS, one row each (aggregated)
    try:
        import json as _j
        _wr = _j.loads((Path(r"C:\Users\Administrator\Desktop\DeepSeek DeskTop\WarRobot\config.json")).read_text(encoding="utf-8"))
        target = float(_wr["sell"].get("target_pct", 25.0))
        stop = float(_wr["sell"].get("stop_pct", 3.0))
    except Exception:
        target = float(CFG.get("sell.target_pct", 12.0))
    try:
        stop = stop
    except Exception:
        stop = float(CFG.get("sell.stop_pct", 3.0))
    _wk = Wdata.weekly()
    _wkpts = [{"symbol": str(x.get("date") or "")[5:], "ts_fa": str(x.get("date") or ""),
               "invested": 0, "proceeds": 0,
               "pnl_rial": float(x.get("pnl_rial") or 0)}
              for x in (_wk.get("points") or [])]
    weekly_chart = svg_chart(_wkpts) if _wkpts else ""
    _rep = Wdata.report()
    report_charts = {}
    for _k, _p in (_rep.get("periods") or {}).items():
        _pts = [{"symbol": str(x.get("date") or "")[5:], "ts_fa": str(x.get("date") or ""),
                 "invested": 0, "proceeds": 0,
                 "pnl_rial": float(x.get("pnl_rial") or 0)}
                for x in (_p.get("points") or [])]
        report_charts[_k] = svg_chart(_pts) if _pts else ""
    sell_rows = Wdata.sell_rows()
    total_realised = round(sum(s["pnl_pct"] for s in sells) / len(sells), 2) if sells else None
    max_pos = int(CFG.get("portfolio.max_positions", 3))
    total_value = sum(p["value"] for p in positions) or 1.0
    target_share = 100.0 / max(1, max_pos)
    for p in positions:
        p["share_pct"] = round(p["value"] * 100.0 / total_value, 1)
        p["off_balance"] = abs(p["share_pct"] - target_share) > 5.0
    # Portfolio-level cumulative P&L since purchase (realised + open).
    port_invested = sum(p["invested"] for p in positions) or 0.0
    port_total = sum(p["total_pnl"] for p in positions)
    port_pct = (port_total / port_invested * 100.0) if port_invested else 0.0
    return render_template(
        "index.html",
        picks=picks, exports=exports, prompts=prompts, positions=positions, sell_rows=sell_rows,
        balance=Wdata.balance(), price_map=Wdata.price_map(),
        fund=Wdata.fund(), report=Wdata.report(), report_charts=report_charts,
        agents=Wdata.agents(), weekly=Wdata.weekly(), webdata=Wdata.webdata(), sell_data=Wdata.sell_data(),
        jalali=Wdata.to_jalali, weekly_chart=weekly_chart,
        pnl_at=Wdata.pnl_at(),
        sells=sells, chart=svg_chart(Wdata.chart_points()), chart_version="v9-warrobot",
        target=target, stop=stop,
        max_pos=max_pos,
        free_slots=max(0, max_pos - len(positions)),
        avg_realised=total_realised,
        target_share=target_share,
        port_total=round(port_total, 0),
        port_pct=round(port_pct, 2),
        port_invested=round(port_invested, 0),
        excluded=sorted(excluded),
        mode=CFG.mode,
        generated=picks.get("generated_at", "-"),
        for_date=picks.get("for_date_g", "-"),
        now=now_tehran().strftime("%Y-%m-%d %H:%M"),
    )


@app.route("/demo")
def demo():
    """Render the sell chart with sample points (clearly labelled as نمونه)."""
    sample = [
        {"ts": "2026-10-19 09:41", "symbol": "نمونه۱", "qty": 1000, "avg": 10000, "price": 11200, "pnl_pct": 12.0},
        {"ts": "2026-10-20 10:12", "symbol": "نمونه۲", "qty": 800, "avg": 20000, "price": 19400, "pnl_pct": -3.0},
        {"ts": "2026-10-21 11:03", "symbol": "نمونه۳", "qty": 500, "avg": 30000, "price": 33600, "pnl_pct": 12.0},
        {"ts": "2026-10-22 09:55", "symbol": "نمونه۴", "qty": 700, "avg": 15000, "price": 16050, "pnl_pct": 7.0},
    ]
    picks = current_picks()
    return render_template("index.html",
                           snap=snap,
                           advice=advice, picks=picks, positions=open_positions(),
                           sell_rows=[], sells=sample, chart=svg_chart(sample),
                           target=float(CFG.get("sell.target_pct", 12.0)),
                           stop=float(CFG.get("sell.stop_pct", 3.0)),
                           max_pos=int(CFG.get("portfolio.max_positions", 3)),
                           free_slots=3, avg_realised=7.0, target_share=33.3, mode="demo",
                           port_total=0, port_pct=0.0, port_invested=0,
                           generated="نمونه", for_date="نمونه",
                           now=datetime.now().strftime("%Y-%m-%d %H:%M"))


@app.route("/api/buy")
def api_buy():
    return jsonify(_read_json(CFG.path("picks_dir") / "today_picks.json", {}))


@app.route("/api/sell")
def api_sell():
    return jsonify({"positions": open_positions(), "realised": realised_series()})


threading.Thread(target=_refresh_live_forever, daemon=True).start()

if __name__ == "__main__":
    # Bind all interfaces: the dashboard is read on the server itself AND from the
    # operator machine over VPN/LAN. 127.0.0.1 made it invisible to both.
    import os as _os
    port = int(_os.environ.get("TSEBOT_WEB_PORT", "5077"))
    app.run(host="0.0.0.0", port=port, debug=False, threaded=True, use_reloader=False)































