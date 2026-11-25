"""Download Tehran options history (all contracts) into data/options/.

Source: TSETMC public closing-price daily list, per instrument code.
The instrument map already contains every option contract we have ever seen
(ض… calls, ط… puts), so this walks it, fetches the full contract life, and
writes one CSV per contract plus a manifest so the job is resumable.

    python options_download.py --limit 200      # try a slice first
    python options_download.py                  # everything (1-2 hours)
"""
from __future__ import annotations

import argparse
import json
import re
import time
from datetime import datetime
from pathlib import Path

import sys
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

BASE = Path(r"C:\Users\Administrator\Desktop\DeepSeek DeskTop\WarRobot")
TESE_ORDERFLOW = BASE.parent / "Tse2" / "orderflow"
sys.path.insert(0, str(TESE_ORDERFLOW))

import tsetmc as api  # noqa: E402

MAP = TESE_ORDERFLOW / "instruments.json"
OUT = BASE / "data" / "options"
MANIFEST = OUT / "manifest.json"


def option_codes() -> list[tuple[str, dict]]:
    raw = json.loads(MAP.read_text(encoding="utf-8"))
    rows = []
    for name, info in raw.items():
        if name.startswith("ض") or name.startswith("ط"):
            rows.append((name, info))
    return rows


def _rows(res) -> list[dict]:
    if isinstance(res, dict):
        return res.get("closingPriceDaily") or []
    return []


def _csv(rows: list[dict]) -> str:
    """TSETMC closingPriceDaily fields -> a clean OHLCV csv."""
    out = ["date,open,high,low,close,yesterday,change_pct,volume,value,trades"]
    for r in sorted(rows, key=lambda x: str(x.get("dEven"))):
        close = r.get("pClosing") or r.get("pDrCotVal") or 0
        prev = r.get("priceYesterday") or 0
        chg = round((close / prev - 1) * 100, 2) if prev else ""
        out.append(",".join(str(x) for x in (
            r.get("dEven", ""), r.get("priceFirst", 0), r.get("priceMax", 0),
            r.get("priceMin", 0), close, prev, chg,
            int(r.get("qTotTran5J") or 0), int(r.get("qTotCap") or 0),
            int(r.get("zTotTran") or 0))))
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--sleep", type=float, default=0.8)
    args = ap.parse_args()

    OUT.mkdir(parents=True, exist_ok=True)
    done = {}
    if MANIFEST.exists():
        try:
            done = json.loads(MANIFEST.read_text(encoding="utf-8"))
        except Exception:
            done = {}

    codes = option_codes()
    print(f"options in map: {len(codes)}  already downloaded: {len(done)}")
    todo = [(n, i) for n, i in codes if n not in done]
    if args.limit:
        todo = todo[:args.limit]

    saved = 0
    for name, info in todo:
        ic = info.get("ins_code")
        if not ic:
            continue
        try:
            rows = _rows(api.closing_daily(ic, 4000))
        except Exception as exc:
            rows = []
        if rows:
            (OUT / f"{name}.csv").write_text(_csv(rows), encoding="utf-8")
            dates = [str(r.get("dEven")) for r in rows]
            done[name] = {"ins_code": ic, "rows": len(rows),
                          "from": min(dates), "to": max(dates),
                          "underlying": re.sub(r"\d+$", "", name),
                          "kind": "call" if name.startswith("ض") else "put"}
            saved += 1
        else:
            done[name] = {"ins_code": ic, "rows": 0, "error": "no_data"}
        if saved % 25 == 0:
            MANIFEST.write_text(json.dumps(done, ensure_ascii=False, indent=1), encoding="utf-8")
            print(f"  progress: {saved} saved / {len(todo)} tried")
        time.sleep(args.sleep)

    MANIFEST.write_text(json.dumps(done, ensure_ascii=False, indent=1), encoding="utf-8")
    ok = sum(1 for v in done.values() if v.get("rows"))
    total_rows = sum(int(v.get("rows") or 0) for v in done.values())
    print(f"DONE: {ok} contracts with data · {total_rows:,} daily rows · {datetime.now():%H:%M:%S}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
