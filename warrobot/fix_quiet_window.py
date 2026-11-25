"""Apply the two hardening fixes for the 1405/08/25 missed pre-open.

1) scheduler: a quiet window 08:42-09:03 — repeating jobs (pnl refresh, exit
   watch, order monitor) are paused so the order job wins the Playwright lock.
   Yesterday/this morning they collided and check2.js returned nothing, which
   turned into "free balance too low (0)" and silently skipped the order.

2) order_candidates._free_balance: retry the live read and fall back to the
   balance saved by the snapshot, instead of returning 0 on a transient error.
"""
from pathlib import Path

# ---------------------------------------------------------------- scheduler
S = Path(r"C:\Users\Administrator\Desktop\DeepSeek DeskTop\WarRobot\scheduler.py")
s = S.read_text(encoding="utf-8")
if "quiet window" not in s:
    s = s.replace('''        # repeating jobs (e.g. the take-profit / stop-loss watch)
        for at_from, at_to, every, job, script in REPEAT:
            if not trading:
                break''',
'''        # repeating jobs (e.g. the take-profit / stop-loss watch)
        for at_from, at_to, every, job, script in REPEAT:
            if not trading:
                break
            # quiet window: the pre-open order needs the browser lock alone
            if "08:42" <= hm <= "09:03":
                continue''')
    S.write_text(s, encoding="utf-8")
    print("scheduler: quiet window 08:42-09:03")

# ------------------------------------------------------------ order_candidates
P = Path(r"C:\Users\Administrator\Desktop\DeepSeek DeskTop\WarRobot\order_candidates.py")
t = P.read_text(encoding="utf-8")
start = t.find("def _free_balance() -> float:")
end = t.find("\ndef ", start + 10)
if start == -1 or end == -1:
    raise SystemExit("free_balance block not found")

new = '''def _snapshot_balance() -> float:
    """Balance saved by the morning/nightly snapshot (state/prices.json)."""
    try:
        import json as _json
        from pathlib import Path as _P
        d = _json.loads((_P(__file__).resolve().parent / "state" / "prices.json"
                         ).read_text(encoding="utf-8"))
        return float((d.get("balance") or {}).get("account_balance") or 0)
    except Exception:
        return 0.0


def _free_balance() -> float:
    """AccountBalance: live broker read (2 tries), then the snapshot fallback.

    A transient browser-lock conflict must never turn into a silent zero: that
    is exactly what skipped the pre-open order on 1405/08/23 and 08/25.
    """
    import time as _time
    for attempt in range(2):
        try:
            p = subprocess.run([NODE, str(PW / "check2.js")], cwd=str(PW),
                               capture_output=True, text=True, timeout=420,
                               encoding="utf-8", errors="replace")
            raw = (p.stdout or "").strip()
            for line in reversed(raw.splitlines()):
                line = line.strip()
                if not line.startswith("{"):
                    continue
                data = json.loads(line)
                flat = json.dumps(data, ensure_ascii=False).replace("\\\\", "")
                m = re.search(r'"AccountBalance":([\\d.]+)', flat)
                if m and float(m.group(1)) > 0:
                    return float(m.group(1))
                break
        except Exception:
            pass
        if attempt == 0:
            _time.sleep(8)
    snap = _snapshot_balance()
    try:
        with LOG.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                                 "symbol": None, "source": "snapshot_balance",
                                 "free_balance": snap, "submitted": False}) + "\\n")
    except Exception:
        pass
    return snap


'''
P.write_text(t[:start] + new + t[end:], encoding="utf-8")
print("order_candidates: balance retry + snapshot fallback")
