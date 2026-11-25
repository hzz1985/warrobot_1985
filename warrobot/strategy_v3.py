"""WarRobot strategy v3 — V2 entry/exit + fundamental, money-flow and sector filters.

Select in config.json:
    "strategy": {"version": "v3", "v2": {...}, "v3": {...}}
The default stays "v1"/"v2"; nothing changes until the owner switches to "v3".

V3 = V2 (ceiling-lock close, value >= 20 bn rial, equal-weight regime > SMA50, no floor-lock in 10
sessions, 10 slots x 10 %, 9 % catastrophe stop, 15-session time exit) plus extra entry filters below.
Exits are exactly V2 (strategy_v2.exit_decision).  Pure functions; the live wiring at the bottom fetches
the extra inputs (rahavard365 EPS history, tsetmc ClientType) defensively and fails OPEN for data gaps
only where the config says so.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, asdict, fields
from pathlib import Path

import strategy_v2 as S2
from strategy_v2 import BUY_FEE, SELL_FEE, exit_decision, stop_level  # noqa: F401  (same exits as v2)


@dataclass
class V3Config(S2.V2Config):
    # ---- fundamentals (point in time: latest Codal statement published before the signal day) ----
    fund_mode: str = "exclude_loss"   # off | exclude_loss (proposed: drop known loss-makers) | require_profit
    pe_max: float | None = None       # drop if TTM P/E above this (None = off)
    growth_min: float | None = None   # drop if YoY cumulative profit growth known and below this
    # ---- money flow (حقیقی) on the signal day ----
    bp_min: float | None = None       # individual buyer power >= bp_min
    inflow_min: float | None = None   # net individual inflow / traded value >= inflow_min
    inflow5_min: float | None = None  # 5-session net individual inflow / value >= inflow5_min
    # ---- sector ----
    sec_mom_min: float | None = None  # sector (others) 20-session return >= this
    # ---- ranking among survivors ----
    rank_by: str = "bp"               # value (v2) | bp (proposed v3) | inflow

    @classmethod
    def from_config(cls, cfg: dict) -> "V3Config":
        st = (cfg or {}).get("strategy") or {}
        raw = {**(st.get("v2") or {}), **(st.get("v3") or {})}
        names = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in raw.items() if k in names})

    def to_dict(self) -> dict:
        return asdict(self)


def _nan(x) -> bool:
    return x is None or (isinstance(x, float) and math.isnan(x))


def passes(r: dict, cfg: V3Config) -> bool:
    """r: candidate row with keys pe (NaN unknown, <=0 loss), growth, bp, inflow, inflow5, sec_mom."""
    pe = r.get("pe")
    if cfg.fund_mode in ("exclude_loss", "require_profit"):
        if _nan(pe):
            if cfg.fund_mode == "require_profit":
                return False
        elif pe <= 0:
            return False
    if cfg.pe_max is not None and not _nan(pe) and pe > cfg.pe_max:
        return False
    if cfg.growth_min is not None and not _nan(r.get("growth")) and r["growth"] < cfg.growth_min:
        return False
    if cfg.bp_min is not None and (_nan(r.get("bp")) or r["bp"] < cfg.bp_min):
        return False
    if cfg.inflow_min is not None and (_nan(r.get("inflow")) or r["inflow"] < cfg.inflow_min):
        return False
    if cfg.inflow5_min is not None and (_nan(r.get("inflow5")) or r["inflow5"] < cfg.inflow5_min):
        return False
    if cfg.sec_mom_min is not None and not _nan(r.get("sec_mom")) and r["sec_mom"] < cfg.sec_mom_min:
        return False
    return True


def pick_candidates(rows: list[dict], cfg: V3Config, regime_ok: bool, held: set) -> list[dict]:
    """Same interface as strategy_v2.pick_candidates; rows carry the extra V3 keys."""
    base = S2.pick_candidates(rows, S2.V2Config(**{k: getattr(cfg, k) for k in S2.V2Config.__dataclass_fields__}
                                                    | {"max_picks": 10 ** 6}), regime_ok, held)
    out = [r for r in base if passes(r, cfg)]
    key = {"value": lambda r: -r["value"],
           "bp": lambda r: -(r.get("bp") or 0.0),
           "inflow": lambda r: -(r.get("inflow") or -9.0)}[cfg.rank_by]
    out.sort(key=key)
    return out[: cfg.max_picks]


def load(path: str | Path) -> tuple[str, V3Config]:
    cfg = json.loads(Path(path).read_text(encoding="utf-8"))
    ver = ((cfg.get("strategy") or {}).get("version") or "v1").lower()
    return ver, V3Config.from_config(cfg)


# ------------------------------------------------------------------ live wiring (used only with v3)
RAHAVARD = "https://rahavard365.com/api/v2"
TSETMC = "https://cdn.tsetmc.com/api"


def _get_json(url: str, timeout: int = 20, referer: str | None = None):
    import urllib.request
    h = {"User-Agent": "Mozilla/5.0"}
    if referer:
        h["Referer"] = referer
    return json.loads(urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=timeout).read())


def live_fundamentals(symbol: str, price: float, cache_dir: str | Path | None = None) -> dict:
    """TTM P/E and YoY growth from the published EPS history (rahavard365 mirror of Codal)."""
    import re
    import urllib.parse
    out = {"pe": float("nan"), "growth": float("nan")}
    try:
        d = _get_json(f"{RAHAVARD}/search?keyword=" + urllib.parse.quote(symbol)).get("data") or []
        norm = lambda s: s.replace("ي", "ی").replace("ك", "ک").replace(" ", "")
        c = [x for x in d if norm(x.get("trade_symbol", "")) == norm(symbol) and x.get("entity_type") == "exchange.asset"]
        if not c:
            return out
        e = _get_json(f"{RAHAVARD}/asset/{c[0]['entity_id']}/eps").get("data") or {}
        rep = {}
        for fy in e.get("eps_fiscal_years") or []:
            for x in fy.get("epses") or []:
                k = int(x.get("fiscal_period") or 0) or 12
                fye = re.search(r"\d{4}-\d{2}-\d{2}", x.get("fiscal_year") or "").group(0)
                rep[(fye, k)] = (float(x.get("pure_realized_eps") or 0), float(x.get("capital") or 0))
        if not rep:
            return out
        fys = sorted({k[0] for k in rep})
        fy, k = max(rep)
        eps, cap = rep[(fy, k)]
        npc, sh = eps * cap / 1000, cap / 1000
        pf = fys[fys.index(fy) - 1] if fys.index(fy) > 0 else None
        if k == 12:
            t = npc
        elif pf and (pf, 12) in rep and (pf, k) in rep:
            t = npc + rep[(pf, 12)][0] * rep[(pf, 12)][1] / 1000 - rep[(pf, k)][0] * rep[(pf, k)][1] / 1000
        else:
            t = npc * 12 / k
        ttm = t / sh if sh else float("nan")
        out["pe"] = price / ttm if ttm > 0 else -1.0
        if pf and (pf, k) in rep:
            pv = rep[(pf, k)][0] * rep[(pf, k)][1] / 1000
            out["growth"] = (npc - pv) / abs(pv) if pv else float("nan")
    except Exception:
        pass
    return out


def live_moneyflow(ins_code: str, value_rial: float) -> dict:
    """Today's حقیقی buyer power and net individual inflow from tsetmc ClientType."""
    out = {"bp": float("nan"), "inflow": float("nan"), "inflow5": float("nan")}
    try:
        rows = _get_json(f"{TSETMC}/ClientType/GetClientTypeHistory/{ins_code}", 30,
                         "https://www.tsetmc.com/").get("clientType") or []
        rows = sorted(rows, key=lambda r: r["recDate"])[-5:]
        if not rows:
            return out
        r = rows[-1]
        if r["buy_I_Count"] and r["sell_I_Count"] and r["sell_I_Value"]:
            out["bp"] = (r["buy_I_Value"] / r["buy_I_Count"]) / (r["sell_I_Value"] / r["sell_I_Count"])
        if value_rial:
            out["inflow"] = (r["buy_I_Value"] - r["sell_I_Value"]) / value_rial
        tv = sum(x["buy_I_Value"] + x["buy_N_Value"] for x in rows)
        if tv:
            out["inflow5"] = sum(x["buy_I_Value"] - x["sell_I_Value"] for x in rows) / tv
    except Exception:
        pass
    return out


def _resolve_ins(sym: str):
    """insCode via the bot's own tsetmc helper (warrobot/src/tsetmc.py) when importable."""
    try:
        import tsetmc as api                      # the bot's module (on sys.path inside distbot)
        inst = api.resolve(sym)
        return str(inst["ins_code"]) if inst else None
    except Exception:
        return None


def filter_scan(cands, config_path: str | Path, daily_dir: str | Path, limit: float = 0.03,
                ins_codes: dict | None = None, log=None):
    """v3 entry filters on distbot.scan_nightly() output (symbol, close, turnover, ...).
    Any version other than v3 -> strategy_v2.filter_scan (unchanged v1/v2 behaviour)."""
    try:
        cfg = json.loads(Path(config_path).read_text(encoding="utf-8"))
    except Exception:
        return cands
    if ((cfg.get("strategy") or {}).get("version") or "v1").lower() != "v3":
        return S2.filter_scan(cands, config_path, daily_dir, limit)
    v3 = V3Config.from_config(cfg)
    v2 = S2.V2Config(**{k: getattr(v3, k) for k in S2.V2Config.__dataclass_fields__})
    v2.max_picks = 10 ** 6                                  # rank after the v3 filters, not before
    base = S2.apply_v2_filters(cands, v2, daily_dir, limit)
    if base is None or not len(base):
        return base
    need_fund = v3.fund_mode != "off" or v3.pe_max is not None or v3.growth_min is not None
    need_flow = (v3.bp_min is not None or v3.inflow_min is not None or v3.inflow5_min is not None
                 or v3.rank_by in ("bp", "inflow"))
    rows = []
    for idx, row in base.iterrows():
        sym = str(row["symbol"])
        r = {"idx": idx, "value": float(row["turnover"])}
        if need_fund:
            r.update(live_fundamentals(sym, float(row.get("close") or 0)))
        if need_flow:
            ins = (ins_codes or {}).get(sym) or _resolve_ins(sym)
            if ins:
                r.update(live_moneyflow(ins, float(row["turnover"])))
        rows.append(r)
    keep = [r for r in rows if passes(r, v3)]
    key = {"value": lambda r: -r["value"],
           "bp": lambda r: -(r.get("bp") if not _nan(r.get("bp")) else -1.0),
           "inflow": lambda r: -(r.get("inflow") if not _nan(r.get("inflow")) else -9.0)}[v3.rank_by]
    keep.sort(key=key)
    out = base.loc[[r["idx"] for r in keep]].copy()
    for col in ("pe", "growth", "bp", "inflow"):
        out[col] = [r.get(col) for r in keep]
    if log:
        log(f"strategy_v3: {len(base)} v2-eligible -> {len(out)} after v3 filters, ranked by {v3.rank_by}")
    return out.head(v3.max_picks)
