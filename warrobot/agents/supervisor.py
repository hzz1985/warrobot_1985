"""Agent supervisor — the only entry point the scheduler needs.

    python -m agents.supervisor            # run every agent once
    python -m agents.supervisor --only a1,a3
    python -m agents.supervisor --no-repair

For each run it writes state/agents/latest.json + a JSONL history, sends at most
one daily digest email (unless everything is quiet), and collects unresolved
problems into state/escalation/queue.json — the single file the architect reads
instead of dozens of chat messages. That is what keeps the token bill down.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / "src"))
STATE = BASE / "state"
AG = STATE / "agents"
ESC = STATE / "escalation"
AG.mkdir(parents=True, exist_ok=True)
ESC.mkdir(parents=True, exist_ok=True)

AGENTS = {"a1": "login_watch", "a2": "preopen_watch",
          "a3": "webapp_watch", "a4": "log_triage",
          "a10": "sale_reconcile", "a11": "sale_price_fix",
          "a12": "order_price",
          "a13": "slots", "a14": "portfolio_pnl", "a15": "sold_pnl",
          "a16": "cash_guard", "a17": "tsetmc_health"}

# trading-related agents stay idle on holidays / Thursday / Friday;
# a3 (price & P&L freshness) and a6 (service guard) keep the data alive
MARKET_ONLY = {"a1", "a2", "a4"}


def run_one(name: str, repair: bool) -> dict:
    try:
        mod = __import__(f"agents.{AGENTS[name]}", fromlist=["run"])
        return mod.run(repair=repair)
    except Exception as exc:
        return {"agent": name, "ok": False, "error": str(exc)[:150],
                "at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}


def escalation_queue(reports: list[dict]) -> dict:
    q_file = ESC / "queue.json"
    try:
        q = json.loads(q_file.read_text(encoding="utf-8"))
    except Exception:
        q = {"items": []}
    items = q.get("items", [])
    for r in reports:
        if r.get("ok"):
            continue
        key = r.get("escalate") or r.get("error") or "unknown"
        if any(i.get("key") == key for i in items):
            continue
        items.append({"key": key, "agent": r.get("agent"),
                      "at": r.get("at"), "detail": {k: r.get(k) for k in
                                                    ("checks", "orders", "http",
                                                     "consistency", "attempts")}})
    q["items"] = items[-30:]
    q_file.write_text(json.dumps(q, ensure_ascii=False, indent=1), encoding="utf-8")
    return q


def digest(reports: list[dict]) -> None:
    try:
        import tsebot.alerts as alerts
    except Exception:
        return
    bad = [r for r in reports if not r.get("ok")]
    icon = "✅" if not bad else "⚠️"
    lines = [f"{'✔' if r.get('ok') else '✘'} {r.get('agent')} "
             f"{r.get('escalate') or r.get('error') or ''}" for r in reports]
    text = "\n".join(lines) + f"\n\nصف escalation: {len(bad)} مورد"
    try:
        import market_cal
        market_cal.send_once(f"agents:{datetime.now().strftime('%Y-%m-%d')}",
                             f"{icon} [WarRobot] وضعیت ایجنت‌ها", text)
    except Exception:
        pass


def main() -> int:
    repair = "--no-repair" not in sys.argv
    only = None
    for a in sys.argv:
        if a.startswith("--only"):
            only = a.split("=", 1)[1] if "=" in a else None
            if only is None and sys.argv.index(a) + 1 < len(sys.argv):
                only = sys.argv[sys.argv.index(a) + 1]
    names = list(AGENTS) if not only else [x.strip() for x in only.split(",") if x.strip() in AGENTS]
    market_open = True
    try:
        import market_cal
        market_open = market_cal.market_day()
    except Exception:
        pass
    if not market_open:
        names = [n for n in names if n not in MARKET_ONLY]

    try:
        from agents import lessons
        lessons.ensure_seed()
    except Exception:
        pass
    reports = [run_one(n, repair) for n in names]
    payload = {"at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
               "repair": repair, "reports": reports,
               "ok": all(r.get("ok") for r in reports)}
    (AG / "latest.json").write_text(json.dumps(payload, ensure_ascii=False, indent=1),
                                    encoding="utf-8")
    with (AG / "history.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(payload, ensure_ascii=False) + "\n")
    q = escalation_queue(reports)

    # routing policy: the architect looks first; the owner gets an email only for
    # critical, still-unresolved failures so he can step in if needed
    critical = {"a1_login", "a2_preopen", "a6_services"}
    urgent = [r for r in reports if not r.get("ok") and r.get("agent") in critical]
    if urgent:
        try:
            import tsebot.alerts as alerts
            body = "\n".join(f"- {r.get('agent')}: {r.get('escalate') or r.get('error')}"
                             for r in urgent)
            body += ("\n\nاین موارد اول به معمار (دیپ‌سیک) ارجاع داده شد."
                     " اگر تا پایان امروز حل نشد، لطفاً وارد سرور شوید.")
            alerts.send("🚨 [WarRobot] مسئلهٔ بحرانی — ارجاع به معمار", body)
        except Exception:
            pass
    if not payload["ok"]:
        digest(reports)
    print(json.dumps({"at": payload["at"], "ok": payload["ok"],
                      "agents": {r.get("agent"): r.get("ok") for r in reports},
                      "escalations": len(q.get("items", []))}, ensure_ascii=False))
    return 0 if payload["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
