"""Download the data backtest_v2.py needs (survivorship-aware universe).

1. Universe = today's market watch stocks (TSE yVal 300 + Farabourse 303)
   + every IRO1/IRO3 instrument in pytse-client's bundled symbol list, including
   OLD insCodes (board moves / delisted names)  ->  ~1,270 instrument codes.
2. Full daily history per insCode from
   https://cdn.tsetmc.com/api/ClosingPrice/GetClosingPriceDailyList/{insCode}/0
   (kept from 2022-06-01).  The endpoint is flaky from abroad (proxy 502s), so
   many threads + automatic re-queue are used.
3. TEDPIX (شاخص کل) from https://api.tgju.org/v1/market/indicator/summary-table-data/bourse
   (the tsetmc index endpoint GetIndexB2History was unreachable from the sandbox).

Usage:  pip install pytse-client pandas ; python fetch_tsetmc_history.py --out DATA_DIR
Outputs DATA_DIR/panel.pkl and DATA_DIR/tedpix_tgju.csv for backtest_v2.py.
"""
from __future__ import annotations

import argparse
import json
import os
import queue
import threading
import time
import urllib.request
from pathlib import Path

import pandas as pd

UA = {"User-Agent": "Mozilla/5.0", "Referer": "https://www.tsetmc.com/"}
MW = ("https://cdn.tsetmc.com/api/ClosingPrice/GetMarketWatch?market=0&industrialGroup="
      "&paperTypes%5B0%5D=1&paperTypes%5B1%5D=2&showTraded=false&withBestLimits=false")
HIST = "https://cdn.tsetmc.com/api/ClosingPrice/GetClosingPriceDailyList/{}/0"
TGJU = "https://api.tgju.org/v1/market/indicator/summary-table-data/bourse"
F = ["dEven", "priceYesterday", "priceFirst", "priceMin", "priceMax", "pClosing", "pDrCotVal",
     "zTotTran", "qTotTran5J", "qTotCap"]
COLS = ["dEven", "py", "o", "l", "h", "c", "last", "ntr", "vol", "val"]


def get(url, timeout=60):
    return json.loads(urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout).read())


def universe() -> dict:
    codes = {}
    for x in get(MW)["marketwatch"]:
        if x["yVal"] in (300, 303):
            codes[x["insCode"]] = (x["lva"], 1 if x["yVal"] == 300 else 2, True)
    try:
        import pytse_client
        pt = json.loads((Path(pytse_client.__file__).parent / "data" / "symbols_name.json").read_text("utf-8"))
        for s, v in pt.items():
            c = v.get("code", "")
            if c.startswith(("IRO1", "IRO3")):
                for ic in [v["index"]] + v.get("old", []):
                    codes.setdefault(ic, (s, 1 if c.startswith("IRO1") else 2, False))
    except Exception as exc:
        print("pytse-client symbol list unavailable (survivorship bias!):", exc)
    return codes


def fetch_all(codes: dict, raw: Path, threads=64, start=20220601, budget=3600):
    raw.mkdir(parents=True, exist_ok=True)
    q = queue.Queue()
    for c in codes:
        if not (raw / f"{c}.json").exists():
            q.put(c)
    t0 = time.time()

    def w():
        while time.time() - t0 < budget:
            try:
                c = q.get_nowait()
            except queue.Empty:
                return
            try:
                rows = get(HIST.format(c), 40).get("closingPriceDaily") or []
                rows = [[r[k] for k in F] for r in rows if r["dEven"] >= start]
                (raw / f"{c}.json").write_text(json.dumps(rows))
            except Exception:
                q.put(c)
    ts = [threading.Thread(target=w, daemon=True) for _ in range(threads)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    print("remaining:", q.qsize())


def build(codes: dict, raw: Path) -> pd.DataFrame:
    norm = lambda s: s.replace("ي", "ی").replace("ك", "ک").strip()
    dfs = []
    for ic, (sym, board, cur) in codes.items():
        f = raw / f"{ic}.json"
        if not f.exists():
            continue
        rows = json.loads(f.read_text())
        if not rows:
            continue
        d = pd.DataFrame(rows, columns=COLS)
        d["ins"], d["sym"], d["board"], d["current"] = ic, norm(sym), board, cur
        dfs.append(d)
    p = pd.concat(dfs)
    p = p[(p.ntr > 0) & (p.py > 0) & (p.h > 0) & (p.c > 0)]
    p["date"] = pd.to_datetime(p.dEven.astype(str))
    p = p.sort_values("val").drop_duplicates(["sym", "date"], keep="last").sort_values(["sym", "date"])
    return p.drop(columns=["dEven"]).reset_index(drop=True)


def tedpix() -> pd.DataFrame:
    f = lambda s: float(s.replace(",", ""))
    rows = get(TGJU)["data"]
    d = pd.DataFrame([{"date": r[6], "open": f(r[0]), "low": f(r[1]), "high": f(r[2]),
                       "close": f(r[3]), "jdate": r[7]} for r in rows])
    d["date"] = pd.to_datetime(d.date)
    return d.sort_values("date")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="data_hark")
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    codes = universe()
    (out / "universe_codes.json").write_text(json.dumps(codes, ensure_ascii=False))
    fetch_all(codes, out / "raw")
    build(codes, out / "raw").to_pickle(out / "panel.pkl")
    tedpix().to_csv(out / "tedpix_tgju.csv", index=False)


if __name__ == "__main__":
    main()
