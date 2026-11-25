"""A4 — log triage: group today's errors, match known lessons, escalate the rest.

Reads the pm2 error logs + the bot's own state files, extracts error signatures,
counts them, attaches the matching lesson (if any), and only leaves truly unknown
signatures in the escalation queue. Optional English summary by the local model
(outside market hours, cached).

    python -m agents.log_triage
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
STATE = BASE / "state"
OUT = STATE / "agents" / "a4_logs.json"
LOGS = [
    Path.home() / ".pm2" / "logs" / "warsched-error.log",
    Path.home() / ".pm2" / "logs" / "tse-web-error.log",
    Path.home() / ".pm2" / "logs" / "warrobot-error.log",
]

PATTERNS = [
    (r"page\.click: Timeout.*?#captcha", "login captcha click timeout"),
    (r"Opening in existing browser session", "chrome profile locked"),
    (r"bind_mismatch wanted=\S+ got=", "symbol bind mismatch"),
    (r"free balance too low", "zero balance read"),
    (r"ReferenceError: (\w+) is not defined", "reference error ~1"),
    (r"'NoneType' object has no attribute", "none attribute"),
    (r"HTTP Error 429", "rate limited 429"),
    (r"Connection refused|WinError 10061", "connection refused"),
    (r"TimeoutError", "timeout"),
    (r"Traceback \(most recent call last\)", "python traceback"),
]


def _tail(path: Path, mb: int = 3) -> str:
    try:
        size = path.stat().st_size
        with path.open("rb") as fh:
            if size > mb * 1024 * 1024:
                fh.seek(-mb * 1024 * 1024, 2)
            return fh.read().decode("utf-8", "replace")
    except Exception:
        return ""


def _recent(line: str, days: int = 2) -> bool:
    """Keep only lines whose timestamp is recent (pm2 logs keep weeks of history)."""
    import datetime as _dt
    m = re.match(r"\[?(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2})", line.strip())
    if not m:
        return True
    try:
        stamp = _dt.datetime.fromisoformat(f"{m.group(1)}T{m.group(2)}")
    except Exception:
        return True
    return (_dt.datetime.now() - stamp).total_seconds() <= days * 86400


def run() -> dict:
    from agents import lessons as lessons
    counts: Counter = Counter()
    samples: dict[str, str] = {}
    for lp in LOGS:
        text = _tail(lp)
        if not text:
            continue
        pending = None
        for line in text.splitlines():
            if not _recent(line):
                continue
            if re.search(r"^[A-Za-z_.]+(Error|Exception):", line.strip()):
                pending = line.strip()[:200]
                continue
            if not re.search(r"Error|Traceback|error|failed|Timeout|refused|429", line):
                continue
            for pat, name in PATTERNS:
                if re.search(pat, line):
                    key = (f"{name} :: {pending.split(':')[0]}" if pending and "traceback" in name
                           else name)
                    counts[key] += 1
                    samples.setdefault(key, (pending or line).strip()[:220])
                    pending = None
                    break
    known, unknown = [], []
    for name, n in counts.most_common(15):
        matches = lessons.find(name + " " + samples.get(name, ""), limit=2)
        item = {"signature": name, "count": n, "sample": samples.get(name, ""),
                "lessons": [{"id": m["id"], "fix": m["fix"], "verify": m.get("verify")}
                            for m in matches]}
        (known if matches else unknown).append(item)

    summary = None if not unknown else None
    if unknown:
        try:
            from agents import llm
            prompt = ("Classify these trading-bot error signatures and suggest the most likely "
                      "single cause for each, in ONE English line per signature:\n"
                      + "\n".join(f"- {u['signature']}: {u['sample'][:160]}" for u in unknown))
            summary = llm.ask(prompt)
        except Exception:
            summary = None

    out = {"agent": "a4_logs", "at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
           "total_error_lines": sum(counts.values()), "known": known,
           "unknown": unknown, "llm_summary": summary,
           "ok": len(unknown) <= 3}
    if unknown:
        out["escalate"] = f"{len(unknown)} unknown error signature(s)"
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    return out


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False)[:800])
