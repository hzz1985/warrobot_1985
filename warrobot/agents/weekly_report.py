"""A7 — automatic weekly report (no architect involved).

Builds once a week (Thursday/Friday night) from the same data the گزارش tab uses:
closed trades from the journal, realised P&L, the equity curve, agent health and
the newest lessons. Output:

    state/report_weekly.json      -> shown in the web report tab
    state/reports/weekly-*.md     -> archive
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
STATE = BASE / "state"
OUT = STATE / "report_weekly.json"
ARCH = STATE / "reports"
ARCH.mkdir(parents=True, exist_ok=True)


def _jsonl(path: Path) -> list[dict]:
    rows = []
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                rows.append(json.loads(line))
            except Exception:
                pass
    return rows


def jal(d) -> str:
    """Gregorian date/str -> Jalali 'YYYY-MM-DD' for the Persian report."""
    try:
        import jdatetime
        from datetime import date as _date, datetime as _dt
        if isinstance(d, str):
            d = _dt.fromisoformat(d[:19]).date()
        elif isinstance(d, _dt):
            d = d.date()
        j = jdatetime.date.fromgregorian(date=d)
        return f"{j.year:04d}-{j.month:02d}-{j.day:02d}"
    except Exception:
        return str(d)


def build(force: bool = False) -> dict:
    now = datetime.now()
    week_start = (now - timedelta(days=6)).strftime("%Y-%m-%d")
    key = now.strftime("%G-W%V")
    prev = {}
    try:
        prev = json.loads(OUT.read_text(encoding="utf-8"))
    except Exception:
        pass
    if not force and prev.get("week") == key:
        return {"skipped": "already generated for this week", "week": key}

    trades = [r for r in _jsonl(STATE / "trade_journal.jsonl")
              if r.get("status") == "closed" and str(r.get("sell_at") or "")[:10] >= week_start]
    realized = [r for r in _jsonl(STATE / "realized.jsonl")
                if str(r.get("at") or "")[:10] >= week_start]
    pnl = round(sum(float(t.get("pnl_rial") or 0) for t in trades), 0)
    wins = sum(1 for t in trades if float(t.get("pnl_rial") or 0) > 0)
    pts = [{"date": str(t.get("sell_at") or "")[:10], "symbol": t.get("symbol"),
            "pnl_rial": float(t.get("pnl_rial") or 0)} for t in trades]
    agents = {}
    try:
        agents = json.loads((STATE / "agents" / "latest.json").read_text(encoding="utf-8"))
    except Exception:
        pass
    lessons = _jsonl(STATE / "lessons.jsonl")[-3:]
    try:
        equity = [json.loads(x) for x in
                  (STATE / "equity_history.jsonl").read_text(encoding="utf-8").splitlines()
                  if x.strip()][-7:]
    except Exception:
        equity = []

    text = (f"گزارش هفتهٔ {jal(week_start)} تا {jal(now.strftime('%Y-%m-%d'))}: "
            f"{len(trades)} معاملهٔ بسته‌شده · "
            f"سود/زیان {pnl:+,.0f} ریال · "
            f"{wins} معاملهٔ سودده")
    payload = {"week": key, "at": now.strftime("%Y-%m-%d %H:%M:%S"),
               "week_start": week_start, "week_end": now.strftime("%Y-%m-%d"),
               "week_start_fa": jal(week_start),
               "week_end_fa": jal(now.strftime("%Y-%m-%d")),
               "trades": len(trades), "wins": wins, "pnl_rial": pnl,
               "points": pts, "realized": len(realized),
               "agents_ok": bool(agents.get("ok")) if agents else None,
               "lessons": [{"symptom": l.get("symptom"), "fix": l.get("fix")}
                           for l in lessons],
               "equity": equity, "text": text}
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")

    md = [f"# {text}", "", f"- هفته: {week_start} → {payload['week_end']}",
          f"- معاملات بسته‌شده: {len(trades)} (سودده: {wins})",
          f"- سود/زیان: {pnl:+,.0f} ریال", ""]
    if trades:
        md.append("| نماد | زمان فروش | سود/زیان ریال |")
        md.append("|---|---|---|")
        for t in trades:
            md.append(f"| {t.get('symbol')} | {str(t.get('sell_at'))[:10]} | "
                      f"{float(t.get('pnl_rial') or 0):+,.0f} |")
    (ARCH / f"weekly-{now.strftime('%Y-%m-%d')}.md").write_text("\n".join(md), encoding="utf-8")
    return payload


if __name__ == "__main__":
    res = build(force="--force" in sys.argv)
    print(json.dumps({k: res.get(k) for k in ("week", "text", "trades", "pnl_rial", "skipped")},
                     ensure_ascii=False)[:500])
