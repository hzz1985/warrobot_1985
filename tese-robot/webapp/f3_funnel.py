# -*- coding: utf-8 -*-
"""F3_QVGM funnel for the dashboard (READ-ONLY with respect to the bot).

Recomputes the stage counts of the nightly plan (state/fund_plan.json) from the bot's own caches:
    total symbols -> with price history -> liquidity universe -> TTM profit > 0 -> 0 < P/E <= pe_max -> ranked
It never fetches prices or fundamentals and never writes inside WarRobot: the price cache
(state/fund_cache/prices) and the rahavard cache (state/fund_cache/rahavard) are only read.
The market-watch universe (insCode -> symbol) is fetched once from TSETMC (read-only GET) and kept in
TeseRobot/webapp/cache/f3_universe.json; the result goes to TeseRobot/webapp/cache/f3_funnel.json.

    python f3_funnel.py            print the funnel (json)
"""
from __future__ import annotations

import json
import math
import os
import sys
import time
from datetime import datetime
from pathlib import Path

WAR = Path(r"C:\Users\Administrator\Desktop\DeepSeek DeskTop\WarRobot")
if not WAR.exists():
    WAR = Path(os.environ.get("WARROBOT_DIR", "."))
HERE = Path(__file__).resolve().parent
CACHE_DIR = HERE / "cache"
OUT = CACHE_DIR / "f3_funnel.json"
UNI = CACHE_DIR / "f3_universe.json"
PLAN = WAR / "state" / "fund_plan.json"
FCACHE = WAR / "state" / "fund_cache"


def _imports():
    if str(WAR) not in sys.path:
        sys.path.append(str(WAR))        # appended: never shadow the webapp's own modules
    import fund_data as FD          # noqa: F401
    import fund_live as FLV
    import strategy_fund as SF
    return FD, FLV, SF


class _ReadOnlyRahavard:
    """Same interface as fund_data.RahavardCache but reads the cache files only (any age, never fetches)."""

    def __init__(self, FD, cache_dir: Path):
        self.FD = FD
        self.dir = Path(cache_dir)
        self.stale: set = set()

    def _doc(self, sym: str, kind: str):
        p = self.dir / kind / f"{self.FD.nsym(sym)}.json"
        if not p.exists():
            return None
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return None

    def eps(self, sym: str):
        d = self._doc(sym, "eps")
        return self.FD.eps_events({"eps": d}) if d else []

    def roe(self, sym: str):
        d = self._doc(sym, "ratios")
        if not d:
            return []
        rows = []
        for r in d if isinstance(d, list) else []:
            try:
                it = {i["field"]["english_title"]: i["value"] for i in r.get("items") or []}
                rows.append({"date": r.get("date"), "roe": it.get("Return on Equity")})
            except Exception:
                continue
        return self.FD.ratio_events(rows)


def _universe(FLV) -> dict:
    """insCode -> symbol; cached in the webapp folder for 20 h (one read-only TSETMC call)."""
    try:
        if UNI.exists() and time.time() - UNI.stat().st_mtime < 20 * 3600:
            return json.loads(UNI.read_text(encoding="utf-8"))["uni"]
    except Exception:
        pass
    try:
        uni = FLV.universe()
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        UNI.write_text(json.dumps({"at": datetime.now().isoformat(timespec="seconds"), "uni": uni},
                                  ensure_ascii=False), encoding="utf-8")
        return uni
    except Exception:
        if UNI.exists():
            return json.loads(UNI.read_text(encoding="utf-8"))["uni"]
        raise


def _hist(ins: str):
    p = FCACHE / "prices" / f"{ins}.json"
    try:
        return json.loads(p.read_text(encoding="utf-8"))["rows"]
    except Exception:
        return None


def compute() -> dict:
    FD, FLV, SF = _imports()
    _, cfg = SF.load_config(WAR / "config.json")
    plan = json.loads(PLAN.read_text(encoding="utf-8")) if PLAN.exists() else {}
    uni = _universe(FLV)
    H = {}
    for ins in uni:
        rows = _hist(ins)
        if rows:
            H[ins] = rows
    cnt: dict = {}
    for rows in H.values():
        for r in rows:
            if r[3] > 0:
                cnt[r[0]] = cnt.get(r[0], 0) + 1
    sessions = sorted(d for d, n in cnt.items() if n >= 20)
    asof = f"{str(sessions[-1])[:4]}-{str(sessions[-1])[4:6]}-{str(sessions[-1])[6:]}"
    rc = _ReadOnlyRahavard(FD, FCACHE / "rahavard")
    rows, liquid, with_fund, profit, pe_ok = [], 0, 0, 0, 0
    for ins, sym in uni.items():
        if ins not in H:
            continue
        pf = FLV.price_features(H[ins], sessions, cfg)
        if not pf or not pf["liquid"]:
            continue
        liquid += 1
        row = dict(sym=FLV.FL.norm(sym), ins=ins, liquid=True, mom=pf["mom"], flow=math.nan, np_ttm=math.nan,
                   mcap=math.nan, yoy=math.nan, roe=math.nan, close=pf["last_close"], val60=pf["val60"])
        eps = rc.eps(sym)
        roe = rc.roe(sym) if eps else []
        sn = FD.snapshot(eps, roe, None, asof) if eps else None
        if sn and sn.get("pub"):
            with_fund += 1
            row.update(np_ttm=sn["np_ttm"], yoy=sn["yoy"], roe=sn["roe"], src=sn["source"], pub=sn["pub"])
            row["mcap"] = FLV.market_cap(pf, sessions, sn["capital"], sn["pub"])
        rows.append(row)
        npt, mc = row["np_ttm"], row["mcap"]
        if npt is not None and not math.isnan(npt) and npt > 0 and row["sym"] not in SF.MANUAL_ONLY:
            profit += 1
            if mc is not None and not math.isnan(mc) and mc > 0 and mc / npt <= cfg.pe_max:
                pe_ok += 1
    ranked = SF.rank(rows, cfg)
    pos = {r["sym"]: r for r in ranked}
    target = plan.get("target") or [r["sym"] for r in ranked[:cfg.n_hold]]
    top = [{"sym": r["sym"], "rank": r["rank"], "score": round(r["score"], 4),
            "ey": round(r["ey"], 4) if not math.isnan(r["ey"]) else None,
            "pe": round(1 / r["ey"], 2) if r["ey"] and not math.isnan(r["ey"]) and r["ey"] > 0 else None,
            "yoy": None if math.isnan(r.get("yoy", math.nan)) else round(r["yoy"], 4),
            "roe": None if math.isnan(r.get("roe", math.nan)) else round(r["roe"], 4),
            "mom": None if math.isnan(r.get("mom", math.nan)) else round(r["mom"], 4),
            "close": r["close"], "pub": r.get("pub")} for r in ranked[:30]]
    out = {
        "computed_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "plan_at": plan.get("at"), "plan_session": plan.get("session"), "asof": asof,
        "variant": cfg.variant, "pe_max": cfg.pe_max, "n_hold": cfg.n_hold, "buffer": cfg.buffer,
        "stages": [
            {"key": "total", "label": "کل نمادهای بورس و فرابورس", "count": len(uni)},
            {"key": "history", "label": "دارای سابقهٔ قیمت", "count": len(H)},
            {"key": "liquid", "label": "جهان نقدشونده (≥۲۰ میلیارد ریال، ≥۸۰٪ روزهای معامله)", "count": liquid},
            {"key": "fund", "label": "دارای دادهٔ بنیادی", "count": with_fund},
            {"key": "profit", "label": "سود خالص TTM مثبت", "count": profit},
            {"key": "pe", "label": f"P/E بین ۰ و {int(cfg.pe_max)}", "count": pe_ok},
            {"key": "ranked", "label": "رتبه‌بندی ترکیبی (E/P، رشد EPS، ROE، مومنتوم)", "count": len(ranked)},
            {"key": "top", "label": f"{cfg.n_hold} سهم برتر (هدف)", "count": len(target)},
        ],
        "plan_liquid": plan.get("liquid"),
        "match_plan_top10": [r["sym"] for r in ranked[:cfg.n_hold]] == list(plan.get("target") or [])[:cfg.n_hold],
        "ranked_top": top,
        "rank_of": {s: pos[s]["rank"] for s in pos},
    }
    return out


def load(max_age_s: float = 6 * 3600) -> dict:
    """cached funnel; recomputed when the plan is newer than the cache or the cache is old."""
    try:
        if OUT.exists():
            d = json.loads(OUT.read_text(encoding="utf-8"))
            plan_m = PLAN.stat().st_mtime if PLAN.exists() else 0
            if OUT.stat().st_mtime >= plan_m and time.time() - OUT.stat().st_mtime < max_age_s:
                return d
    except Exception:
        pass
    d = compute()
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    return d


if __name__ == "__main__":
    d = load(max_age_s=0) if "--force" in sys.argv else load()
    d = dict(d)
    d.pop("rank_of", None)
    print(json.dumps(d, ensure_ascii=False, indent=1))
