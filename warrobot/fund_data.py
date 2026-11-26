"""Point-in-time fundamentals for the fundamentals strategy (strategy_fund.py) — shared by the live path and
the backtest so both compute exactly the same numbers.

Sources
  rahavard365  /api/v2/asset/<id>/eps              every published EPS report: publish day, fiscal year,
                                                   period (3/6/9/12 months, 0 = AGM), cumulative EPS, capital
               /api/v2/asset/<id>/financialratios  annual ROE (dated by publication)
  Codal        ن-10 extractions (codal_fund.extract format) when a cache folder is available
               (warrobot/data_v3/codal_crawl.py output: ext/<TracingNo>.json).  Optional.

Point in time: a report counts on signal day d when its publish day <= d (orders go to session d+1).
Several versions of one (fiscal year, period) — audited / corrected — : the newest version published on or
before d wins.  Nothing here raises to the caller; missing data -> NaN -> neutral rank / excluded by gates.
stdlib only (runs on the server's Python without extra packages).
"""
from __future__ import annotations

import json
import math
import os
import re
import time
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path

NAN = float("nan")
RH = "https://rahavard365.com/api/v2"
UA = {"User-Agent": "Mozilla/5.0"}


def _d(s) -> str | None:
    m = re.search(r"(\d{4}-\d{2}-\d{2})", str(s or ""))
    return m.group(1) if m else None


def _ord(d: str) -> int:
    return date.fromisoformat(d).toordinal()


def nsym(s: str) -> str:
    return (s or "").replace("ي", "ی").replace("ك", "ک").replace("\u200c", "").replace(" ", "").strip()


def isnan(x) -> bool:
    return x is None or (isinstance(x, float) and math.isnan(x))


# --------------------------------------------------------------------------- rahavard EPS
def eps_events(doc: dict) -> list[dict]:
    """rahavard eps document -> sorted report list [{pub, fy, m, eps, capital, np}] (np in rial)."""
    out = []
    e = (doc or {}).get("eps") or doc or {}
    for fy in e.get("eps_fiscal_years") or []:
        for x in fy.get("epses") or []:
            pub, fyd = _d(x.get("date_time")), _d(x.get("fiscal_year") or fy.get("fiscal_year"))
            m = int(x.get("fiscal_period") or 0)
            if not pub or not fyd:
                continue
            if m == 0:                      # AGM-approved annual figure
                m = 12
            if m not in (3, 6, 9, 12):
                continue
            eps, cap = x.get("pure_realized_eps"), x.get("capital")
            if eps is None or not cap:
                continue
            out.append(dict(pub=pub, fy=fyd, m=m, eps=float(eps), capital=float(cap),
                            np=float(eps) * float(cap) / 1000.0))
    out.sort(key=lambda z: (z["pub"], z["fy"], z["m"]))
    return out


def _known(events: list[dict], asof: str) -> dict:
    """(fy, m) -> newest version published on or before asof."""
    k = {}
    for x in events:
        if x["pub"] <= asof:
            k[(x["fy"], x["m"])] = x          # events are sorted by pub: later wins
    return k


def _prev_fy(fys: list[str], fy: str) -> str | None:
    for f in fys:
        if 350 <= _ord(fy) - _ord(f) <= 380:
            return f
    return None


def eps_snapshot(events: list[dict], asof: str) -> dict:
    """TTM net profit, YoY growth of the newest cumulative period, share base, newest publish day."""
    k = _known(events, asof)
    out = dict(np_ttm=NAN, ttm_exact=False, yoy=NAN, capital=NAN, pub=None, cum_eps=NAN, months=0, fy=None,
               n_reports=len(k), loss_reports=sum(1 for v in k.values() if v["np"] < 0))
    if not k:
        return out
    fy, m = max(k, key=lambda z: (z[0], z[1]))
    cur = k[(fy, m)]
    out.update(capital=cur["capital"], pub=max(v["pub"] for v in k.values()), cum_eps=cur["eps"], months=m, fy=fy)
    fys = sorted({f for f, _ in k})
    pf = _prev_fy(fys, fy)
    if m == 12:
        out["np_ttm"], out["ttm_exact"] = cur["np"], True
    elif pf and (pf, 12) in k and (pf, m) in k:
        out["np_ttm"], out["ttm_exact"] = cur["np"] + k[(pf, 12)]["np"] - k[(pf, m)]["np"], True
    else:
        out["np_ttm"] = cur["np"] * 12.0 / m
    if pf and (pf, m) in k:
        pv = k[(pf, m)]["np"]
        if pv > 0:
            out["yoy"] = cur["np"] / pv - 1.0
        elif pv < 0:
            out["yoy"] = (cur["np"] - pv) / abs(pv)
    return out


# --------------------------------------------------------------------------- rahavard ROE (annual)
def ratio_events(rows: list) -> list[tuple[str, float]]:
    ev = []
    for r in rows or []:
        d, roe = _d(r.get("date")), r.get("roe")
        if d and roe is not None:
            ev.append((d, float(roe) / 100.0))
    return sorted(ev)


def roe_asof(ev: list[tuple[str, float]], asof: str) -> float:
    v = NAN
    for d, x in ev:
        if d <= asof:
            v = x
    return v


# --------------------------------------------------------------------------- Codal (optional)
def codal_events(exts: list[dict]) -> list[dict]:
    """codal_fund ن-10 extractions of ONE symbol -> [{pub, np_ttm, yoy, roe, exact}] sorted by pub."""
    try:
        import codal_fund as C
    except Exception:
        return []
    ev = []
    for ex in exts:
        try:
            st = C.parse_statement(ex)
            if not st:
                continue
            mt = C.statement_metrics(st)
        except Exception:
            continue
        if isnan(mt.get("np_ttm")) or not mt.get("pub"):
            continue
        ev.append(dict(pub=mt["pub"], pend=mt["pend"], np_ttm=mt["np_ttm"] * 1e6, yoy=mt["np_yoy"],
                       roe=mt["roe"], exact=not mt["ttm_annualised"], equity=mt["equity"]))
    ev.sort(key=lambda z: (z["pub"], z["pend"]))
    return ev


def codal_asof(ev: list[dict], asof: str) -> dict | None:
    """newest reporting period known at asof (a later-published older period never overrides)."""
    best = None
    eq = NAN
    for x in ev:
        if x["pub"] > asof:
            break
        if not isnan(x.get("equity")):
            eq = x["equity"]
        if best is None or x["pend"] >= best["pend"]:
            best = dict(x)
    if best is not None and isnan(best.get("roe")) and not isnan(eq) and eq > 0:
        best["roe"] = best["np_ttm"] / (eq * 1e6)
    return best


# --------------------------------------------------------------------------- combined snapshot
def snapshot(eps_ev: list[dict], roe_ev: list, codal_ev: list[dict] | None, asof: str) -> dict:
    """Best point-in-time view: Codal when its newest report is at least as recent as rahavard's,
    otherwise rahavard. Returns np_ttm (rial), yoy, roe, capital (rial), pub, source."""
    r = eps_snapshot(eps_ev, asof)
    out = dict(np_ttm=r["np_ttm"], yoy=r["yoy"], roe=roe_asof(roe_ev, asof), capital=r["capital"],
               pub=r["pub"], ttm_exact=r["ttm_exact"], cum_eps=r["cum_eps"], source="rahavard" if r["pub"] else None,
               loss_reports=r["loss_reports"], n_reports=r["n_reports"])
    c = codal_asof(codal_ev, asof) if codal_ev else None
    if c and (not r["pub"] or c["pub"] >= r["pub"]):
        out["np_ttm"] = c["np_ttm"]
        out["ttm_exact"] = c["exact"]
        if not isnan(c.get("yoy")):
            out["yoy"] = c["yoy"]
        if not isnan(c.get("roe")):
            out["roe"] = c["roe"]
        out["pub"] = max(c["pub"], r["pub"] or c["pub"])
        out["source"] = "codal"
    return out


# --------------------------------------------------------------------------- live fetch with daily cache
class RahavardCache:
    """Daily file cache for rahavard365 (asset id, eps, financialratios).  A fetch failure keeps the last
    cached copy (any age) and marks the symbol stale; callers decide what stale means (fail-safe)."""

    def __init__(self, cache_dir, ttl_h: float = 20.0, timeout: float = 25.0, budget_s: float = 600.0):
        self.dir = Path(cache_dir)
        for sub in ("eps", "ratios", "ids"):
            (self.dir / sub).mkdir(parents=True, exist_ok=True)
        self.ttl = ttl_h * 3600
        self.timeout = timeout
        self.deadline = time.time() + budget_s
        self.stale: set[str] = set()
        self.errors: dict[str, str] = {}

    def _get(self, url):
        last = ""
        for a in range(3):
            if time.time() > self.deadline:
                raise RuntimeError("budget")
            try:
                req = urllib.request.Request(url, headers=UA)
                return json.loads(urllib.request.urlopen(req, timeout=self.timeout).read())
            except Exception as e:
                last = str(e)[:120]
                time.sleep(1 + 2 * a)
        raise RuntimeError(last)

    def _fresh(self, p: Path) -> bool:
        return p.exists() and time.time() - p.stat().st_mtime < self.ttl

    def asset_id(self, sym: str):
        p = self.dir / "ids" / f"{nsym(sym)}.json"
        if p.exists():
            return json.loads(p.read_text(encoding="utf-8")).get("id")
        d = self._get(f"{RH}/search?keyword=" + urllib.parse.quote(sym)).get("data") or []
        c = [x for x in d if nsym(x.get("trade_symbol", "")) == nsym(sym) and x.get("entity_type") == "exchange.asset"]
        c.sort(key=lambda x: (x.get("type") != "سهام", x.get("unlisted_item", False)))
        aid = c[0]["entity_id"] if c else None
        p.write_text(json.dumps({"sym": sym, "id": aid}, ensure_ascii=False), encoding="utf-8")
        return aid

    def _doc(self, sym: str, kind: str, path: str):
        p = self.dir / kind / f"{nsym(sym)}.json"
        if self._fresh(p):
            return json.loads(p.read_text(encoding="utf-8"))
        try:
            aid = self.asset_id(sym)
            if not aid:
                return None
            d = self._get(f"{RH}/asset/{aid}/{path}").get("data")
            p.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
            return d
        except Exception as e:
            self.errors[sym] = f"{kind}: {e}"[:160]
            if p.exists():
                self.stale.add(sym)
                return json.loads(p.read_text(encoding="utf-8"))
            return None

    def eps(self, sym: str) -> list[dict]:
        d = self._doc(sym, "eps", "eps")
        return eps_events({"eps": d}) if d else []

    def roe(self, sym: str) -> list:
        d = self._doc(sym, "ratios", "financialratios")
        if not d:
            return []
        rows = []
        for r in d if isinstance(d, list) else []:
            try:
                it = {i["field"]["english_title"]: i["value"] for i in r.get("items") or []}
                rows.append({"date": r.get("date"), "roe": it.get("Return on Equity")})
            except Exception:
                continue
        return ratio_events(rows)


def codal_ext_index(ext_dir) -> dict:
    """symbol -> list of ن-10 extractions from a codal_crawl ext/ folder (optional source)."""
    out: dict = {}
    p = Path(ext_dir) if ext_dir else None
    if not p or not p.exists():
        return out
    for f in p.glob("*.json"):
        try:
            ex = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        if ex.get("lt") == 6 and ex.get("sym") and ex.get("pub_g"):
            out.setdefault(nsym(ex["sym"]), []).append(ex)
    return out
