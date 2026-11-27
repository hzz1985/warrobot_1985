"""Live signal generation for the fundamentals strategy (strategy_fund.py).  Never places an order.

    python fund_live.py --plan            nightly (after the close): writes state/fund_plan.json
    python fund_live.py --plan --force    rebalance now even if the schedule says no
    python fund_live.py --status          print the last plan
    python fund_live.py --plan --out X    rehearsal: write the plan to X, no state change, no log line, own price
                                          cache (state/fund_cache/prices_rehearsal) so tonight's plan re-fetches

What it does (same numbers as the backtest, research/fund/bt_fund.py):
  1. universe  = TSETMC market watch stocks (TSE 300 / IFB 303), symbols without a trailing digit
  2. prices    = TSETMC GetClosingPriceDailyList/<insCode>/<n> (daily file cache, one call per symbol per day)
                 -> 60-session liquidity, 126..5-session momentum, total-return chain for market cap
  3. fundamentals = rahavard365 EPS + annual ROE (fund_data.RahavardCache, daily cache) and, when the Codal crawl
                 folder exists, Codal ن-10 extractions (better TTM / YoY / ROE)
  4. rank / select (strategy_fund.rank / select) against the fund's own ledger (fund_ledger.py)
  5. sizing    = stock budget (fund_ledger.capital: (1 - afran_share) x total in replace mode) / n_hold per name;
                 informational only - fund_orders.py re-sizes from the live buying power on every run

Fail-safe: if prices or fundamentals are missing for too much of the liquid universe, or any step raises,
the plan is "hold" — no buys, no sells — and the reason is written to the plan and the log.
"""
from __future__ import annotations

import concurrent.futures as cf
import json
import math
import os
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))
import fund_data as FD          # noqa: E402
import fund_ledger as FL        # noqa: E402
import strategy_fund as SF      # noqa: E402
import war2_stop as W2          # noqa: E402

STATE = BASE / "state"
CACHE = STATE / "fund_cache"
PRICE_CACHE = CACHE / "prices"   # --out rehearsals use their own folder so they never feed tonight's plan a stale close
PLAN = STATE / "fund_plan.json"
LOG = STATE / "fund_log.jsonl"
TSE = "https://cdn.tsetmc.com/api"
HDR = {"User-Agent": "Mozilla/5.0", "Referer": "https://www.tsetmc.com/"}
MW = (TSE + "/ClosingPrice/GetMarketWatch?market=0&industrialGroup=&paperTypes%5B0%5D=1&paperTypes%5B1%5D=2"
      "&showTraded=false&withBestLimits=false")
HIST_N = 260
MIN_PRICE_COVERAGE = 0.90     # of market-watch stocks with a usable history
MIN_FUND_COVERAGE = 0.60      # of liquid stocks with a fundamentals snapshot


def log(event: str, **kw) -> None:
    STATE.mkdir(parents=True, exist_ok=True)
    rec = {"at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "event": event, **kw}
    with LOG.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
    print(json.dumps(rec, ensure_ascii=False, default=str)[:400], flush=True)


def _get(url, timeout=30, tries=3):
    last = ""
    for a in range(tries):
        try:
            return json.loads(urllib.request.urlopen(urllib.request.Request(url, headers=HDR), timeout=timeout).read())
        except Exception as e:
            last = str(e)[:120]
            time.sleep(1 + 2 * a)
    raise RuntimeError(last)


# ------------------------------------------------------------------ market data
def universe() -> dict:
    """insCode -> symbol for listed stocks (TSE main + IFB), trailing-digit symbols (block / alt boards) dropped."""
    out = {}
    for x in _get(MW, timeout=60).get("marketwatch") or []:
        if x.get("yVal") in (300, 303, "300", "303"):
            sym = str(x.get("lva") or "").strip()
            if sym and not sym[-1].isdigit():
                out[str(x["insCode"])] = sym
    return out


def history(ins: str, day: str) -> list:
    """[[dEven, py, c, ntr, val], ...] oldest first; cached per insCode per day."""
    p = PRICE_CACHE / f"{ins}.json"
    if p.exists():
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            if d.get("day") == day:
                return d["rows"]
        except Exception:
            pass
    rows = _get(f"{TSE}/ClosingPrice/GetClosingPriceDailyList/{ins}/{HIST_N}").get("closingPriceDaily") or []
    rows = sorted([[int(r["dEven"]), float(r["priceYesterday"] or 0), float(r["pClosing"] or 0),
                    float(r["zTotTran"] or 0), float(r["qTotCap"] or 0)] for r in rows])
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"day": day, "rows": rows}), encoding="utf-8")
    return rows


def price_features(rows: list, sessions: list[int], cfg: SF.FundConfig) -> dict | None:
    """liquidity / momentum / total-return chain on the market's session calendar (same rules as backtest)."""
    by = {r[0]: r for r in rows if r[3] > 0 and r[1] > 0 and r[2] > 0}
    if not by:
        return None
    last60 = sessions[-60:]
    traded = sum(1 for d in last60 if d in by)
    val60 = sum(by[d][4] for d in last60 if d in by) / 60.0
    tr, chain, prev_c = 1.0, {}, None
    for d in sessions:
        r = by.get(d)
        if r:
            g = r[2] / r[1] - 1
            if (g > 0.6 or g < -0.6) and prev_c:
                g = r[2] / prev_c - 1
                if g > 0.6 or g < -0.6:
                    g = 0.0
            tr *= 1 + g
            prev_c = r[2]
        chain[d] = tr
    mom = math.nan
    if len(sessions) > cfg.mom_lb:
        a, b = chain[sessions[-1 - cfg.mom_lb + 0]], chain[sessions[-1 - cfg.mom_skip]]
        mom = b / a - 1 if a > 0 else math.nan
    return dict(liquid=(traded / 60.0 >= cfg.min_traded_frac and val60 >= cfg.min_value_rial and sessions[-1] in by),
                val60=val60, traded_frac=traded / 60.0, mom=mom, chain=chain, by=by,
                last_close=by[max(by)][2], last_day=max(by))


def market_cap(pf: dict, sessions: list[int], capital: float, pub: str) -> float:
    """shares at the report x close on the report day x total return since (backtest formula)."""
    if not capital or (isinstance(capital, float) and math.isnan(capital)) or not pub:
        return math.nan
    pd_ = int(pub.replace("-", ""))
    days = [d for d in sessions if d <= pd_ and d in pf["by"]]
    if not days:
        return math.nan
    k = days[-1]
    return capital / 1000.0 * pf["by"][k][2] * pf["chain"][sessions[-1]] / pf["chain"][k]


# ------------------------------------------------------------------ War2 stop (war2_stop.py)
def _war2(plan: dict, st: dict, uni: dict, H: dict, sessions: list, w2cfg: dict, fetch: dict) -> None:
    """judge every F3 holding on tonight's close; fills plan risk / stops / war2_basis (never orders)."""
    sess = sessions[-1]
    ins_of = {FL.norm(sym): ins for ins, sym in uni.items()}
    _, pos = (fetch.get("positions") or FL.positions_snapshot)()
    leg = FL.legacy_v3(set(pos)) if pos else FL.legacy_v3(set())
    fund = {s: p for s, p in pos.items() if s not in SF.MANUAL_ONLY and s not in W2.MANUAL_ONLY and s not in leg}
    w = W2.w2_state(st)
    basis = W2.update_basis(w["basis"], fund, sess)
    pend = W2.pending(st)
    risk, stops = [], []
    for s, p in sorted(fund.items()):
        r = W2.judge(s, p, H.get(ins_of.get(s)) or [], sess, basis.get(s), w2cfg["stop_fixed_pct"])
        if s in pend:
            r["status"] = "pending"
        risk.append(r)
        if r["status"] == "triggered":
            stops.append({"symbol": s, "status": "new", "trigger_session": sess, "close": r["close"],
                          "cost_ps": r["cost_ps"], "stop_price": r["stop_price"], "adj": r["adj"],
                          "qty": r["qty"], "pnl_pct": r["pnl_pct"], "reason": W2.REASON})
    for s, v in pend.items():
        stops.append(dict({k: v.get(k) for k in ("trigger_session", "close", "cost_ps", "stop_price", "adj",
                                                  "cycle", "qty", "pnl_pct")},
                          symbol=s, status="pending", held=s in fund, reason=W2.REASON))
    plan["risk"] = risk
    plan["stops"] = stops
    plan["war2_basis"] = basis
    for r in risk:
        if r["status"] in ("no_cost", "no_close_today"):
            plan["notes"].append(f"{r['symbol']}: War2 stop not judged tonight ({r['status']})")


def _war2_finish(plan: dict, st: dict) -> dict:
    """cycle tag for tonight's new stops + the stopped-this-cycle list (after the rebalance decision)."""
    committed = st.get("last_rebalance_session")
    cyc = plan.get("session") if plan.get("action") == "rebalance" else committed
    for x in plan.get("stops") or []:
        if x.get("status") == "new":
            x["cycle"] = cyc
    if (plan.get("war2") or {}).get("active"):
        plan["stopped_this_cycle"] = sorted(W2.stopped_in_cycle(st, committed) |
                                            {x["symbol"] for x in plan.get("stops") or [] if x.get("status") == "new"})
    return plan


# ------------------------------------------------------------------ plan
def build_plan(force: bool = False, now: datetime | None = None, fetch=None) -> dict:
    """fetch: optional dict of injectable data functions for tests
       {universe(), history(ins, day), rahavard (RahavardCache-like), codal_index, total_assets(), positions()}"""
    st = FL.load_state()
    plan = _build_plan(force, now, fetch, st)
    return _war2_finish(plan, st)


def _build_plan(force: bool, now: datetime | None, fetch, st: dict) -> dict:
    now = now or datetime.now()
    raw, cfg = SF.load_config(BASE / "config.json")
    fc = FL.fund_config()
    plan = {"at": now.strftime("%Y-%m-%d %H:%M:%S"), "variant": cfg.variant, "enabled": fc["enabled"],
            "mode": fc["mode"], "capital_share": fc["capital_share"], "action": "hold", "buys": [], "sells": [],
            "target": [], "notes": []}
    w2cfg = W2.config(BASE / "config.json")
    plan["war2"] = {"name": w2cfg["name"], "stop_fixed_pct": w2cfg["stop_fixed_pct"], "active": w2cfg["active"]}
    if w2cfg["active"]:             # until tonight's judgement runs, the pending stops from the state stand
        plan["stops"] = [dict({k: v.get(k) for k in ("trigger_session", "close", "cost_ps", "stop_price", "cycle",
                                                     "qty")}, symbol=s, status="pending", reason=W2.REASON)
                         for s, v in W2.pending(st).items()]
    fetch = fetch or {}
    day = now.strftime("%Y%m%d")
    try:
        uni = (fetch.get("universe") or universe)()
        hist = fetch.get("history") or history
        H = {}
        with cf.ThreadPoolExecutor(6) as ex:
            futs = {ex.submit(hist, ins, day): ins for ins in uni}
            for f in cf.as_completed(futs):
                try:
                    H[futs[f]] = f.result()
                except Exception:
                    pass
        cov = len(H) / max(1, len(uni))
        plan["price_coverage"] = round(cov, 3)
        if cov < MIN_PRICE_COVERAGE:
            plan["notes"].append(f"price coverage {cov:.0%} < {MIN_PRICE_COVERAGE:.0%}: hold")
            return plan
        cnt: dict = {}
        for rows in H.values():
            for r in rows:
                if r[3] > 0:
                    cnt[r[0]] = cnt.get(r[0], 0) + 1
        sessions = sorted(d for d, n in cnt.items() if n >= 20)        # market sessions
        if len(sessions) < cfg.mom_lb + 2:
            plan["notes"].append("too few sessions in history: hold")
            return plan
        plan["session"] = sessions[-1]
        # schedule: sessions since the last rebalance (fund_state.json)
        last = st.get("last_rebalance_session")
        since = len([d for d in sessions if last and d > int(last)]) if last else 10 ** 6
        plan["sessions_since_rebalance"] = since if last else None
        rebalance = force or since >= cfg.rebalance_sessions
        if w2cfg["active"]:         # War2 stop: judged every night on the close (prices only, before fundamentals)
            try:
                _war2(plan, st, uni, H, sessions, w2cfg, fetch)
            except Exception as exc:
                plan["notes"].append(f"War2 stop error ({str(exc)[:120]}): no new stop tonight, pending stops kept")
        rc = fetch.get("rahavard") or FD.RahavardCache(CACHE / "rahavard")
        cidx = fetch.get("codal_index")
        if cidx is None:
            cidx = FD.codal_ext_index(fc.get("codal_ext_dir"))
        asof = f"{str(sessions[-1])[:4]}-{str(sessions[-1])[4:6]}-{str(sessions[-1])[6:]}"
        rows, liquid, with_fund = [], 0, 0
        for ins, sym in uni.items():
            if ins not in H:
                continue
            pf = price_features(H[ins], sessions, cfg)
            if not pf or not pf["liquid"]:
                continue
            liquid += 1
            row = dict(sym=FL.norm(sym), ins=ins, liquid=True, mom=pf["mom"], flow=math.nan, np_ttm=math.nan,
                       mcap=math.nan, yoy=math.nan, roe=math.nan, close=pf["last_close"], val60=pf["val60"])
            try:
                eps = rc.eps(sym)
                roe = rc.roe(sym) if eps else []
            except Exception:
                eps, roe = [], []
            cev = FD.codal_events(cidx.get(FD.nsym(sym), [])) if cidx else None
            sn = FD.snapshot(eps, roe, cev, asof) if (eps or cev) else None
            if sn and sn.get("pub"):
                with_fund += 1
                row.update(np_ttm=sn["np_ttm"], yoy=sn["yoy"], roe=sn["roe"], src=sn["source"], pub=sn["pub"])
                row["mcap"] = market_cap(pf, sessions, sn["capital"], sn["pub"])
            rows.append(row)
        fcov = with_fund / max(1, liquid)
        plan["liquid"] = liquid
        plan["fund_coverage"] = round(fcov, 3)
        stale = len(getattr(rc, "stale", ()) or ())
        plan["rahavard_stale"] = stale
        if fcov < MIN_FUND_COVERAGE:
            plan["notes"].append(f"fundamentals coverage {fcov:.0%} < {MIN_FUND_COVERAGE:.0%}: hold")
            return plan
        ranked = SF.rank(rows, cfg)
        plan["ranked_top"] = [{k: (round(v, 4) if isinstance(v, float) else v) for k, v in r.items()
                               if k in ("sym", "rank", "score", "ey", "yoy", "roe", "mom", "src", "pub", "close")}
                              for r in ranked[:30]]
        if not rebalance:
            plan["notes"].append(f"not a rebalance session ({since} of {cfg.rebalance_sessions})")
            return plan
        if cfg.regime_ma:
            plan["notes"].append("regime filter configured but not implemented live: hold")   # F5 is not the live variant
            return plan
        held = FL.fund_symbols()
        stop_names = {x["symbol"] for x in plan.get("stops") or []}     # War2: being stopped out -> not a pick
        if stop_names:
            plan["notes"].append("War2 stops left out of the target: " + ", ".join(sorted(stop_names)))
        target = SF.select([r for r in ranked if FL.norm(r["sym"]) not in stop_names],
                           [s for s in held if s not in stop_names], cfg)
        if len(target) < cfg.n_hold:
            plan["notes"].append(f"only {len(target)} eligible names")
        known = {r["sym"]: r for r in rows}
        sells = []
        for s in held:
            if s in target or s in stop_names:      # a stopped name is sold by the War2 stop order, never twice
                continue
            if s not in known:          # no data for a holding -> never sell blind
                plan["notes"].append(f"{s}: held but no data today, kept")
                target.append(s)
                continue
            sells.append({"symbol": s, "qty": FL.fund_qty(s), "reason": "rebalance",
                          "ref_price": known[s]["close"]})
        total = (fetch.get("total_assets") or FL.total_assets)()
        fund_equity = FL.fund_equity(total)
        if not fetch.get("total_assets"):
            try:
                plan["capital"] = FL.capital()      # snapshot view; fund_orders.py re-sizes live on every run
            except Exception:
                pass
        per_name = fund_equity / cfg.n_hold if fund_equity > 0 else 0.0
        buys = []
        excluded = FL.v3_symbols()
        for s in target:
            if s in held or s in SF.MANUAL_ONLY:
                continue
            if s in excluded:
                plan["notes"].append(f"{s}: legacy V3 position, not a fund slot until it exits")
                continue
            r = known[s]
            buys.append({"symbol": s, "budget_rial": round(per_name, 0), "ref_price": r["close"],
                         "rank": next((x["rank"] for x in ranked if x["sym"] == s), None),
                         "max_budget_by_liquidity": round(cfg.liq_cap * r["val60"], 0)})
            if w2cfg["active"] and r["close"]:
                buys[-1]["stop_price_est"] = round(W2.stop_price(r["close"] * (1 + W2.FEE_BUY),
                                                                 w2cfg["stop_fixed_pct"]), 0)
        target = [FL.norm(s) for s in target]
        for b in buys:
            b["symbol"] = FL.norm(b["symbol"])
        for s_ in sells:
            s_["symbol"] = FL.norm(s_["symbol"])
        for r_ in plan.get("ranked_top") or []:
            r_["sym"] = FL.norm(r_["sym"])
        plan.update(action="rebalance", target=target, buys=buys, sells=sells,
                    total_assets=round(total, 0), fund_equity=round(fund_equity, 0), per_name=round(per_name, 0))
        return plan
    except Exception as exc:
        plan["action"] = "hold"
        plan["notes"].append(f"error: {str(exc)[:200]}")
        return plan


def main(argv: list[str]) -> int:
    if "--status" in argv:
        print(PLAN.read_text(encoding="utf-8") if PLAN.exists() else "{}")
        return 0
    if "--plan" not in argv:
        print(__doc__)
        return 0
    fc = FL.fund_config()
    if not fc["enabled"] and "--force" not in argv:
        log("fund_plan_skipped", reason="strategy.fund.enabled is false")
        return 0
    global PRICE_CACHE
    out = PLAN
    if "--out" in argv:             # rehearsal: write the plan elsewhere, touch no state (deploy tests)
        out = Path(argv[argv.index("--out") + 1])
        PRICE_CACHE = CACHE / "prices_rehearsal"
    plan = build_plan(force="--force" in argv)
    basis = plan.pop("war2_basis", None)
    STATE.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(plan, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    if basis is not None and out == PLAN:
        st = FL.load_state()
        W2.w2_state(st)["basis"] = basis
        FL.save_state(st)
    if out == PLAN:
        log("fund_plan", action=plan["action"], buys=[b["symbol"] for b in plan["buys"]],
            sells=[s["symbol"] for s in plan["sells"]], notes=plan["notes"],
            war2_stops=[(x["symbol"], x["status"]) for x in plan.get("stops") or []])
    else:
        print(json.dumps({"out": str(out), "action": plan["action"], "stops": plan.get("stops"),
                          "stopped_this_cycle": plan.get("stopped_this_cycle"), "notes": plan["notes"][:10]},
                         ensure_ascii=False, default=str)[:3000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
