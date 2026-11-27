"""Fund-strategy capital split + who owns which position (Hark, F3_QVGM live since 2026-11-28).

config.json  strategy.fund  (all keys optional; default = OFF, nothing changes for V3):
  enabled          false        master switch for fund_live.py / fund_orders.py
  mode             "alongside"  "alongside": fund gets capital_share of total assets, V3 the rest
                                "replace":   fund is the only automatic strategy; V3 places no new buys
                                             (its exits still run for the legacy V3 positions, see below)
  capital_share    0.3          fund share of the stock budget in alongside mode (replace -> 1.0)
  live_orders      false        false = fund_orders.py runs flow.js with FLOW_DRY=1 (no real order)
  variant          "F3_QVGM"    see strategy_fund.VARIANTS
  manage_afran     false        true = افران is part of the capital: target afran_share of the total stays
                                in افران, the bot SELLS only the excess above that target (never below it)
                                and never buys افران on its own (profit_transfer is capped at the target)
  afran_share      1/3          افران target share of the total capital (manage_afran only)
  min_trade_rial   50e6         no order below this value (buys, top-ups, افران excess sales)
  topup_drift      0.25         an existing fund position is topped up only when it sits this far
                                (share of the per-name target) below its target - no churn on small drift
  afran_min_excess_pct 0.02     افران excess is sold only when it is >= this share of the total capital
  codal_ext_dir    null         optional Codal crawl ext/ folder (better TTM / YoY / ROE)

Capital (manage_afran, replace mode):
    total        = buying power (cash) + افران market value + fund (F3) holdings + open fund buy orders
    afran target = afran_share x total;  stock budget = (1 - afran_share) x total
    per name     = stock budget / n_hold   (recomputed on every run, so a deposit or a profitable sale
                                            raises the next buys automatically)
  خساپا is never part of anything.  Legacy V3 positions (state/fund_state.json "legacy_v3", e.g. آباد at
  the switch) are NOT in the base and NOT a fund slot until they are gone; exit_engine keeps judging them
  with the V3 exits (9 % stop, 15-session time exit).  Once the broker no longer shows a legacy symbol it
  leaves the list by itself, so the fund may buy that symbol later and it is then a fund position.

Ownership in replace mode: every broker position that is not MANUAL_ONLY and not legacy V3 is a fund
position (the bot is the only automatic buyer).  The old ledger file state/fund_positions.json is still
read in alongside mode / when the fund is disabled (a config typo can never make V3 dump fund holdings).
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent
STATE = BASE / "state"
LEDGER = STATE / "fund_positions.json"
FSTATE = STATE / "fund_state.json"
AFRAN = "افران"
MANUAL_ONLY = {"افران", "خساپا"}
NEVER = {"خساپا"}                       # not even part of the capital
DEFAULTS = {"enabled": False, "mode": "alongside", "capital_share": 0.3, "live_orders": False,
            "variant": "F3_QVGM", "codal_ext_dir": None, "manage_afran": False,
            "afran_share": 1.0 / 3.0, "min_trade_rial": 50e6, "topup_drift": 0.25,
            "afran_min_excess_pct": 0.02}


def norm(sym) -> str:
    """broker / TSETMC spelling: Arabic yeh / kaf -> Persian (TSETMC lists «شغدير», the broker shows «شغدیر»)."""
    return str(sym or "").replace("ي", "ی").replace("ك", "ک").strip()


def _raw_config(path=None) -> dict:
    try:
        return json.loads(Path(path or BASE / "config.json").read_text(encoding="utf-8"))
    except Exception:
        return {}


def fund_config(path=None) -> dict:
    raw = ((_raw_config(path).get("strategy") or {}).get("fund")) or {}
    out = dict(DEFAULTS)
    out.update({k: v for k, v in raw.items() if k in DEFAULTS and v is not None})
    out["enabled"] = bool(out["enabled"])
    out["live_orders"] = bool(out["live_orders"])
    out["manage_afran"] = bool(out["manage_afran"])
    if out["mode"] not in ("alongside", "replace"):
        out["mode"] = "alongside"
    for k, lo, hi in (("capital_share", 0.0, 1.0), ("afran_share", 0.0, 0.95), ("topup_drift", 0.0, 1.0),
                      ("afran_min_excess_pct", 0.0, 1.0)):
        try:
            out[k] = min(hi, max(lo, float(out[k])))
        except Exception:
            out[k] = DEFAULTS[k]
    try:
        out["min_trade_rial"] = max(0.0, float(out["min_trade_rial"]))
    except Exception:
        out["min_trade_rial"] = DEFAULTS["min_trade_rial"]
    if out["mode"] == "replace":
        out["capital_share"] = 1.0
    return out


def replace_active(path=None) -> bool:
    fc = fund_config(path)
    return fc["enabled"] and fc["mode"] == "replace"


def raw_fund_enabled(path=None) -> bool:
    """config says the fund is on (used by fail-safe branches: when in doubt, V3 must NOT trade)."""
    try:
        return bool((((_raw_config(path).get("strategy") or {}).get("fund")) or {}).get("enabled"))
    except Exception:
        return False


# ------------------------------------------------------------------ old ledger (alongside mode)
def load() -> dict:
    try:
        d = json.loads(LEDGER.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def save(d: dict) -> None:
    STATE.mkdir(parents=True, exist_ok=True)
    tmp = LEDGER.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(LEDGER)


def record_fill(sym: str, side: str, qty: float, price: float, fee_rial: float = 0.0, at: str | None = None) -> dict:
    d = load()
    p = d.get(sym) or {"qty": 0, "cost_rial": 0.0, "entry": at or datetime.now().strftime("%Y-%m-%d"), "tag": "fund"}
    q = float(p.get("qty") or 0)
    if side == "buy":
        p["cost_rial"] = float(p.get("cost_rial") or 0) + qty * price + fee_rial
        p["qty"] = q + qty
    else:
        left = max(0.0, q - qty)
        p["cost_rial"] = float(p.get("cost_rial") or 0) * (left / q) if q > 0 else 0.0
        p["qty"] = left
    if p["qty"] <= 0:
        d.pop(sym, None)
    else:
        d[sym] = p
    save(d)
    return p


def load_state() -> dict:
    try:
        d = json.loads(FSTATE.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def save_state(st: dict) -> None:
    STATE.mkdir(parents=True, exist_ok=True)
    tmp = FSTATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(FSTATE)


# ------------------------------------------------------------------ broker snapshots (no browser)
def _read(p: Path) -> dict:
    try:
        return json.loads(p.read_text(encoding="utf-8-sig"))
    except Exception:
        return {}


def positions_snapshot() -> tuple[str, dict]:
    """(at, {symbol: {qty, value, price}}) from the newest of the data agent's portfolio card
    (state/webdata/portfolio.json, every 10 min in session) and state/pnl.json."""
    best_at, best = "", {}
    for p in (STATE / "webdata" / "portfolio.json", STATE / "pnl.json"):
        d = _read(p)
        rows = d.get("positions")
        if not isinstance(rows, list) or not d.get("at"):
            continue
        if str(d["at"]) <= best_at:
            continue
        out = {}
        for r in rows:
            s = norm(r.get("symbol"))
            if not s:
                continue
            qty = float(r.get("qty") or r.get("quantity") or 0)
            val = float(r.get("value") or 0)
            px = float(r.get("price") or 0) or (val / qty if qty else 0.0)
            if qty > 0:
                out[s] = {"qty": qty, "value": val or qty * px, "price": px}
                # War2 stop basis: the portfolio card's avg_cost is the raw broker average; pnl.json's avg_cost is
                # already the broker's fee-inclusive break-even average -> keep it only as avg_cost_fee
                try:
                    if p.name == "portfolio.json" and float(r.get("avg_cost") or 0) > 0:
                        out[s]["avg_cost"] = float(r["avg_cost"])
                    fee_avg = float(r.get("avg_cost_fee") or (r.get("avg_cost") if p.name == "pnl.json" else 0) or 0)
                    if fee_avg > 0:
                        out[s]["avg_cost_fee"] = fee_avg
                except Exception:
                    pass
        best_at, best = str(d["at"]), out
    return best_at, best


def cash_snapshot() -> float:
    """buying power from the newest local snapshot (live reads go through order_candidates._free_balance)."""
    cands = []
    b = _read(STATE / "webdata" / "balance.json")
    if b.get("at") and (b.get("purchasing_power") or b.get("account_balance")) is not None:
        cands.append((str(b["at"]), float(b.get("purchasing_power") or b.get("account_balance") or 0)))
    pr = _read(STATE / "prices.json")
    bal = pr.get("balance") or {}
    if pr.get("at") and bal.get("account_balance") is not None:
        cands.append((str(pr["at"]), float(bal.get("account_balance") or 0)))
    return max(cands)[1] if cands else 0.0


def fresh_cash_snapshot(max_age_min: float = 10.0) -> float:
    """buying power from the data agent's balance card (state/webdata/balance.json, every 5 min in session) when it
    is at most max_age_min old (server clock), else 0. Fallback when the live broker read fails."""
    b = _read(STATE / "webdata" / "balance.json")
    try:
        age = (datetime.now() - datetime.strptime(str(b["at"])[:19], "%Y-%m-%d %H:%M:%S")).total_seconds() / 60.0
        if 0 <= age <= max_age_min and b.get("ok", True):
            return float(b.get("purchasing_power") or b.get("account_balance") or 0)
    except Exception:
        pass
    return 0.0


def broker_snapshot() -> tuple[float, dict, dict]:
    """(cash, {symbol: value}, {symbol: qty}) without خساپا (kept for older callers)."""
    _, pos = positions_snapshot()
    val = {s: p["value"] for s, p in pos.items() if s not in NEVER}
    qty = {s: p["qty"] for s, p in pos.items() if s not in NEVER}
    return cash_snapshot(), val, qty


# ------------------------------------------------------------------ ownership
def legacy_v3(held: set | None = None) -> set:
    """V3 positions still open since the switch (exit_engine owns them). Without a snapshot: the full list."""
    lst = {norm(s) for s in (load_state().get("legacy_v3") or [])}
    if held is None:
        _, pos = positions_snapshot()
        if not pos:
            return lst
        held = set(pos)
    return lst & set(held)


def fund_symbols() -> list[str]:
    if replace_active():
        _, pos = positions_snapshot()
        leg = legacy_v3(set(pos))
        return sorted(s for s in pos if s not in MANUAL_ONLY and s not in leg)
    return [s for s, p in load().items() if float(p.get("qty") or 0) > 0 and s not in MANUAL_ONLY]


def fund_qty(sym: str) -> int:
    if replace_active():
        _, pos = positions_snapshot()
        return int((pos.get(sym) or {}).get("qty") or 0)
    return int(float((load().get(sym) or {}).get("qty") or 0))


def v3_excluded() -> set:
    """symbols V3 must ignore (never judge for exits, never top up, never count): fund positions + MANUAL_ONLY."""
    return set(fund_symbols()) | MANUAL_ONLY


def v3_disabled_in_config() -> bool:
    try:
        v3 = ((_raw_config().get("strategy") or {}).get("v3")) or {}
        return v3.get("enabled") is False
    except Exception:
        return False


def v3_buys_enabled() -> bool:
    return not (replace_active() or v3_disabled_in_config())


def v3_scale() -> float:
    fc = fund_config()
    if not fc["enabled"]:
        return 1.0
    return 0.0 if fc["mode"] == "replace" else 1.0 - fc["capital_share"]


def v3_symbols() -> set:
    """positions the fund must never buy into: legacy V3 (replace) / V3 holdings (alongside)."""
    if replace_active():
        return legacy_v3()
    _, val, qty = broker_snapshot()
    held = {s for s in set(val) | set(qty) if (qty.get(s) or 0) > 0 or (val.get(s) or 0) > 0}
    return held - v3_excluded()


# ------------------------------------------------------------------ capital
def capital(cash: float | None = None, pending_buys_rial: float = 0.0) -> dict:
    """The capital split for this moment (see module doc)."""
    fc = fund_config()
    at, pos = positions_snapshot()
    cash = cash_snapshot() if cash is None else float(cash)
    leg = legacy_v3(set(pos))
    af = pos.get(AFRAN) or {}
    fund = {s: p["value"] for s, p in pos.items() if s not in MANUAL_ONLY and s not in leg} \
        if fc["mode"] == "replace" else {s: (pos.get(s) or {}).get("value", 0.0) for s in fund_symbols()}
    afran_value = float(af.get("value") or 0) if fc["manage_afran"] else 0.0
    total = max(0.0, cash) + afran_value + sum(fund.values()) + max(0.0, pending_buys_rial)
    share = fc["afran_share"] if fc["manage_afran"] else 0.0
    stock_budget = total * (1.0 - share) * fc["capital_share"]
    return {"at": at, "cash": round(cash, 0), "afran_qty": float(af.get("qty") or 0),
            "afran_price": float(af.get("price") or 0), "afran_value": round(afran_value, 0),
            "fund": {s: round(v, 0) for s, v in fund.items()}, "fund_value": round(sum(fund.values()), 0),
            "pending_buys": round(pending_buys_rial, 0), "legacy_v3": sorted(leg),
            "legacy_value": round(sum((pos.get(s) or {}).get("value", 0) for s in leg), 0),
            "total": round(total, 0), "afran_target": round(total * share, 0),
            "stock_budget": round(stock_budget, 0)}


def total_assets() -> float:
    return capital()["total"]


def fund_equity(total: float) -> float:
    fc = fund_config()
    share = fc["afran_share"] if fc["manage_afran"] else 0.0
    return max(0.0, total * (1.0 - share) * fc["capital_share"])


def fund_value(prices: dict | None = None) -> float:
    return capital()["fund_value"]


def afran_transfer_cap() -> float:
    """profit_transfer: how much may still go into افران (manage_afran: up to the target, never above)."""
    fc = fund_config()
    if not (fc["enabled"] and fc["manage_afran"]):
        return float("inf")
    c = capital()
    return max(0.0, c["afran_target"] - c["afran_value"])
